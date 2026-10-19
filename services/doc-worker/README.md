# doc-worker

Turns uploads into text Jarvis can search and read. It takes `parse_file` jobs from the
`arq:docs` queue, then:

1. reuses the earlier parse if the same user already uploaded the same bytes (by SHA-256);
2. parses the file: Docling for PDFs and images (layout-aware tables, OCR for scans),
   MarkItDown for Word, PowerPoint, Excel, CSV, HTML and as the PDF fallback, plain decoding for
   text and code;
3. splits the Markdown into chunks that follow headings and spreadsheet sheets, keeping tables
   whole where they fit and repeating the header row where they don't;
4. embeds the chunks through LiteLLM (`jarvis-embed`), reusing cached vectors by text hash, and
   stores them in `file_chunks` with a full-text index;
5. marks the file ready and tells the user's browser over the WebSocket.

If the embedder is down the file is still marked ready and keyword search works; a job every ten
minutes embeds whatever is missing, and re-embeds chunks made by an older model after the
embedder changes.

The Docker image installs Docling with CPU PyTorch and bakes its models in (a few GB). Build
with `--build-arg DOCLING=false` for a small image without it. Generating files (python-pptx,
openpyxl, python-docx, WeasyPrint) comes with the skills library in phase 3.
