from fastapi import FastAPI, UploadFile, File
import os
from services.extract_metadata import AudiobookMetadataExtractor
from services.extract_activation_bytes import AAXProcessor

app = FastAPI()


@app.get("/")
def read_root():
    return {"message": "Hello, World!"}


@app.post("/upload/file/aax")
def upload_file_aax(file: UploadFile = File(...)):
    file_path = os.path.join("uploads", file.filename)
    with open(file_path, "wb") as f:
        f.write(file.file.read())

    # Extract chapters
    extractor = AudiobookMetadataExtractor(file_path)
    extractor.extract_full_metadata()
    processor = AAXProcessor("/app/audible_rainbow_tables")
    result = processor.process_aax_file(file_path)

    return {
        "message": "File uploaded successfully",
        "metadata": extractor.metadata,
        "activation_bytes": result,
    }
