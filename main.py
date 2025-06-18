from fastapi import FastAPI, UploadFile, File, Request
import os
from services.extract_metadata import AudiobookMetadataExtractor
from services.extract_activation_bytes import AAXProcessor
from models import AudiobookMetadata
from fastapi.templating import Jinja2Templates


app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.get("/")
def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def read_root():
    return {"message": "OK"}


@app.post("/upload/file/aax")
def upload_file_aax(file: UploadFile = File(...)):
    file_path = os.path.join("uploads", file.filename)
    with open(file_path, "wb") as f:
        f.write(file.file.read())

    # Extract metadata using activation bytes
    processor = AAXProcessor()
    result = processor.get_activation_bytes(file_path)

    extractor = AudiobookMetadataExtractor(file_path)
    extractor.get_complete_metadata_using_activation_bytes(result["activation_bytes"])

    return extractor.get_complete_metadata()
