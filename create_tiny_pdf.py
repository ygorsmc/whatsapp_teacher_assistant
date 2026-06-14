import fitz
doc = fitz.open()
page = doc.new_page()
page.insert_text((50, 50), "Hello world, this is a tiny PDF for testing.")
doc.save("tiny.pdf")
