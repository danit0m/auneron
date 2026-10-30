from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi import status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.account_writers_busy import AccountWritersBusy
from app.core.account_writers_busy import busy_detail
from app.core.account_writers_busy import busy_headers
from app.database.database import get_db
from app.services.import_service import import_clients


router = APIRouter()

# Teto do CSV legado, aplicado lendo em blocos (sem confiar em
# Content-Length) antes de import_clients e, portanto, antes do lock.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024

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
    try:
        return await run_in_threadpool(_upload_file_sync, file, db)
    finally:
        await file.close()


def _upload_file_sync(file: UploadFile, db: Session):
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
            temp_path = Path(buffer.name)
            _copy_bounded(file.file, buffer)

        result = import_clients(str(temp_path), db)

        return {
            "arquivo": original_name,
            "resultado": result,
            "status": "sucesso",
        }

    except AccountWritersBusy:
        # Lock não obtido: import_clients já fez rollback, nada gravado.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=busy_detail(),
            headers=busy_headers(),
        ) from None

    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _copy_bounded(source, target) -> None:
    # Nunca lê mais que MAX_UPLOAD_BYTES + 1 no total; o erro não ecoa o corpo.
    written = 0

    while True:
        chunk = source.read(
            min(UPLOAD_CHUNK_BYTES, MAX_UPLOAD_BYTES + 1 - written)
        )

        if not chunk:
            return

        written += len(chunk)

        if written > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Arquivo CSV excede o limite de 5 MiB.",
            )

        target.write(chunk)
