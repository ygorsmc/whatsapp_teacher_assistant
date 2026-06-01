---
name: mlops-patterns
description: Principles and decision-making for MLOps, LLMOps, RAG architecture, Model Inference Optimization, Data Drift and AI FinOps.
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

# MLOps & LLMOps Patterns

> **MANDATORY**: Follow these architectural guidelines when designing ML systems, AI-serving APIs, RAG pipelines, and LLM orchestrations.

## 1. LLMOps (RAG Architecture & Generative Models)

### RAG Architecture
- **Vector Store Decoupling**: Do not hardcode specific libraries like Pinecone, Qdrant, or ChromaDB in LLM business logic. Use dependency injection and generic interfaces (`BaseVectorStore`).
- **Observability**: Log ALL raw prompts, native exceptions (JSON errors), API latency (ms), token consumption (Prompt/Completion), and exact model identifiers/versions.
- **Resiliency (Backoffs)**: External LLMs fail during peak hours. Integrate Rate Limiting and fallback mechanisms using libraries like `tenacity` (Exponential Backoff).

## 2. Ingestion & Classic ML (YOLO, OpenCV, Classification)

### Tracking and Versioning
- **Never store native weights locally in Git**: Models (`.pt`, `.bin`) in development must be tracked via artifact caches (DVC, Azure ML Datastores) or specialized hubs.
- **Monitor Silent Deterioration**: Track performance decay caused by real-world distribution shifts using Drift Analysis (e.g., Kolmogorov-Smirnov/K-L Divergence) on incoming vectors from the telemetry infrastructure.

### Resource Maximization (Inference Inbound)
- **Batching is Mandatory**: For object detection (YOLO) or embedders, purely avoid passive loops (`for image in array: model.predict(image)`). Send global batched arrays (`model.predict([list])`) to saturate the framework pipeline. GPUs inherently hate queue underutilization.
- **Optimized Export**: When viable and ready for CD, recommend fast and compatible serialization formats like **ONNX** or **TensorRT** to decouple massive PyTorch weights in pure production inference.

## 3. FinOps for AI (Azure ML / AWS SageMaker etc.)

### The Golden Rule of Consumption
- Sourcing locally hosted Open Source models (e.g., Ollama or vLLM) seems like "Zero cost per Token", but you are managing continuous hardware billing (idle compute).
- Always enforce routines that clean/stop endpoints down to "Scale to Zero" (`min_instances = 0` / Time-based Auto-Shutdowns / Scheduled Logic Apps).
- Enforce metrics to choose when massive and expensive models (OpenAI GPT-4/Ultra Models) can step aside for efficient smaller distilled models filled with few-shots.

## Anti-Patterns (FORBIDDEN)
❌ **Hardcoded API keys within Client SDKs.**
❌ Omitting `usage_metadata` for token tracking (local or cloud), generating financial black-boxes.
❌ Testing just `assert response is not None` on LLM outputs instead of using controlled analytical Golden Datasets (RAGAs / LLM-as-a-judge).
