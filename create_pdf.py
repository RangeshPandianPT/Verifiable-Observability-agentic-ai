from markdown_pdf import Section, MarkdownPdf

pdf = MarkdownPdf(toc_level=2)
with open("Verifiable_Observability_Project_Overview.md", "r", encoding="utf-8") as f:
    markdown_text = f.read()

pdf.add_section(Section(markdown_text))
pdf.save("Verifiable_Observability_Project_Overview.pdf")
print("Successfully generated PDF: Verifiable_Observability_Project_Overview.pdf")
