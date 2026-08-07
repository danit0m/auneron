import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi import status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.services.import_service import import_clients


router = APIRouter()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


@router.post("/upload/")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    temp_path: Path | None = None

    try:
        original_name = Path(file.filename or "").name

        if not original_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nome de arquivo inválido.",
            )

        if Path(original_name).suffix.lower() != ".csv":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Envie um arquivo CSV.",
            )

        with NamedTemporaryFile(
            mode="wb",
            prefix="auneron_",
            suffix=".csv",
            dir=UPLOAD_DIR,
            delete=False,
        ) as buffer:
            shutil.copyfileobj(file.file, buffer)
            temp_path = Path(buffer.name)

        result = import_clients(str(temp_path), db)

        return {
            "arquivo": original_name,
            "resultado": result,
            "status": "sucesso",
        }

    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    finally:
        await file.close()

        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
