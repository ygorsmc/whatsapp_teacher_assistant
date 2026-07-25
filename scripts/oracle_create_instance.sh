#!/usr/bin/env bash
# ============================================================================
# Cria a VM ARM Ampere A1 (4 OCPU / 24 GB) do Always Free, com RETRY infinito
# contra o "Out of host capacity" — o gargalo real do free tier da Oracle.
#
# O que ele faz:
#   - enumera TODOS os availability domains da região e vai rodando entre eles
#   - acha sozinho a imagem Ubuntu 22.04 aarch64 (a menos que você fixe IMAGE_ID)
#   - injeta um cloud-init que instala Docker + abre as portas 80/443 no iptables
#   - re-tenta a cada RETRY_INTERVAL segundos até a capacidade aparecer
#
# Pré-requisitos:
#   - OCI CLI instalado e autenticado (`oci setup config`) — ou rode no OCI Cloud
#     Shell, que já vem autenticado.
#   - Uma VCN com subnet PÚBLICA já criada (wizard "VCN with Internet Connectivity").
#   - Sua chave SSH pública.
#
# Uso:
#   export OCI_COMPARTMENT_ID=ocid1.tenancy.oc1..xxxx   # tenancy = raiz serve
#   export OCI_SUBNET_ID=ocid1.subnet.oc1..xxxx
#   export OCI_SSH_KEY=$HOME/.ssh/id_rsa.pub
#   ./scripts/oracle_create_instance.sh
# ============================================================================
set -uo pipefail

# ── Config (tudo sobrescrevível por env var) ────────────────────────────────
COMPARTMENT_ID="${OCI_COMPARTMENT_ID:-}"
SUBNET_ID="${OCI_SUBNET_ID:-}"
SSH_KEY="${OCI_SSH_KEY:-$HOME/.ssh/id_rsa.pub}"
DISPLAY_NAME="${OCI_DISPLAY_NAME:-whatsapp-a1}"
SHAPE="${OCI_SHAPE:-VM.Standard.A1.Flex}"
OCPUS="${OCI_OCPUS:-4}"                    # 4 = toda a cota A1 do free numa VM só
MEM_GB="${OCI_MEM_GB:-24}"                 # 24 idem
BOOT_GB="${OCI_BOOT_GB:-100}"             # min 50, free total de bloco = 200
IMAGE_ID="${OCI_IMAGE_ID:-}"              # vazio = descobre Ubuntu 22.04 aarch64
OS_NAME="${OCI_OS_NAME:-Canonical Ubuntu}"
OS_VER="${OCI_OS_VERSION:-22.04}"
RETRY_INTERVAL="${OCI_RETRY_INTERVAL:-60}" # segundos entre tentativas

command -v oci >/dev/null || { echo "ERRO: OCI CLI não encontrado. Instale ou use o Cloud Shell."; exit 1; }
[ -n "$COMPARTMENT_ID" ] || { echo "ERRO: defina OCI_COMPARTMENT_ID"; exit 1; }
[ -n "$SUBNET_ID" ]     || { echo "ERRO: defina OCI_SUBNET_ID"; exit 1; }
[ -f "$SSH_KEY" ]       || { echo "ERRO: chave SSH pública não encontrada em $SSH_KEY"; exit 1; }

# ── cloud-init: Docker + firewall (Ubuntu vem com iptables bloqueando tudo) ──
CLOUD_INIT="$(mktemp)"
trap 'rm -f "$CLOUD_INIT"' EXIT
cat > "$CLOUD_INIT" <<'YAML'
#cloud-config
package_update: true
packages: [ca-certificates, curl, netfilter-persistent, iptables-persistent]
runcmd:
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  - chmod a+r /etc/apt/keyrings/docker.asc
  - echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
  - apt-get update
  - apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  - usermod -aG docker ubuntu
  # Abre 80/443 ANTES da regra REJECT padrão da imagem Oracle (posição 6).
  - iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  - iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  - netfilter-persistent save
YAML

# ── Descobre a imagem Ubuntu 22.04 aarch64 (compatível com o shape A1) ───────
if [ -z "$IMAGE_ID" ]; then
  echo "[*] Procurando imagem mais recente: $OS_NAME $OS_VER (aarch64, shape $SHAPE)..."
  IMAGE_ID="$(oci compute image list \
    --compartment-id "$COMPARTMENT_ID" \
    --operating-system "$OS_NAME" \
    --operating-system-version "$OS_VER" \
    --shape "$SHAPE" \
    --sort-by TIMECREATED --sort-order DESC \
    --query 'data[0].id' --raw-output 2>/dev/null)"
  [ -n "$IMAGE_ID" ] && [ "$IMAGE_ID" != "null" ] || { echo "ERRO: não achei imagem. Fixe OCI_IMAGE_ID."; exit 1; }
fi
echo "[*] Imagem: $IMAGE_ID"

# ── Enumera os availability domains da região ────────────────────────────────
mapfile -t ADS < <(oci iam availability-domain list \
  --compartment-id "$COMPARTMENT_ID" --query 'data[].name' --raw-output 2>/dev/null \
  | tr -d '[],"' | sed '/^\s*$/d' | awk '{$1=$1};1')
[ "${#ADS[@]}" -gt 0 ] || { echo "ERRO: não consegui listar availability domains."; exit 1; }
echo "[*] Availability domains: ${ADS[*]}"
echo "[*] Shape $SHAPE — ${OCPUS} OCPU / ${MEM_GB} GB / boot ${BOOT_GB} GB"

# ── Loop de retry: gira entre os ADs até a capacidade aparecer ───────────────
attempt=0
while true; do
  for AD in "${ADS[@]}"; do
    attempt=$((attempt + 1))
    echo "──────────────────────────────────────────────────────────────"
    echo "[tentativa $attempt @ $(date '+%H:%M:%S')] AD: $AD"
    OUT="$(oci compute instance launch \
      --compartment-id "$COMPARTMENT_ID" \
      --availability-domain "$AD" \
      --shape "$SHAPE" \
      --shape-config "{\"ocpus\": $OCPUS, \"memoryInGBs\": $MEM_GB}" \
      --image-id "$IMAGE_ID" \
      --subnet-id "$SUBNET_ID" \
      --assign-public-ip true \
      --display-name "$DISPLAY_NAME" \
      --ssh-authorized-keys-file "$SSH_KEY" \
      --boot-volume-size-in-gbs "$BOOT_GB" \
      --user-data-file "$CLOUD_INIT" \
      --wait-for-state RUNNING 2>&1)"
    RC=$?
    if [ $RC -eq 0 ]; then
      echo "✅ VM criada e RUNNING!"
      INSTANCE_ID="$(echo "$OUT" | grep -oE 'ocid1\.instance[^"]+' | head -1)"
      echo "[*] Instance OCID: $INSTANCE_ID"
      echo "[*] IP público:"
      oci compute instance list-vnics --instance-id "$INSTANCE_ID" \
        --query 'data[0]."public-ip"' --raw-output 2>/dev/null
      echo "──────────────────────────────────────────────────────────────"
      echo "Próximo passo: ssh ubuntu@<ip> e siga docs/ORACLE_DEPLOY.md"
      exit 0
    fi
    if echo "$OUT" | grep -qiE "out of host capacity|internalerror|500"; then
      echo "   … sem capacidade nesse AD (esperado). Próximo."
    else
      echo "   ⚠ Erro NÃO relacionado a capacidade:"
      echo "$OUT" | tail -5
      echo "   (corrija a config acima — não adianta re-tentar). Abortando."
      exit $RC
    fi
  done
  echo "[*] Rodou todos os ADs sem capacidade. Aguardando ${RETRY_INTERVAL}s..."
  sleep "$RETRY_INTERVAL"
done
