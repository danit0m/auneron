from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / "frontend"
DIST_ROOT = FRONTEND_ROOT / "dist"

ALLOWED_ENV_FILES = {
    "backend/.env.example",
    "backend/.env.test.example",
    "frontend/.env.example",
}

FORBIDDEN_FRONTEND_SOURCE_TOKENS = (
    "VITE_AUNERON_API_KEY",
    "VITE_API_KEY",
    "VITE_ELEVATED_DEV_CODE",
    "ELEVATION_SESSION_KEY",
    "createElevationExpiration",
    "isDevelopmentElevation",
)

FORBIDDEN_BUNDLE_TOKENS = (
    "AUNERON_API_KEY",
    "VITE_ELEVATED_DEV_CODE",
    "ELEVATION_SESSION_KEY",
    "CHANGE_ME_WITH_THE_SAME_LOCAL_BACKEND_API_KEY",
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    return [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def check_tracked_env_files(files: list[str]) -> list[str]:
    violations: list[str] = []

    for relative_path in files:
        name = Path(relative_path).name

        if name == ".env" or name.startswith(".env."):
            if relative_path not in ALLOWED_ENV_FILES:
                violations.append(
                    f"Arquivo de ambiente rastreado: {relative_path}"
                )

    return violations


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def check_frontend_source(files: list[str]) -> list[str]:
    violations: list[str] = []

    for relative_path in files:
        if not relative_path.startswith("frontend/"):
            continue

        path = REPO_ROOT / relative_path

        if not path.is_file():
            continue

        content = read_text(path)

        for token in FORBIDDEN_FRONTEND_SOURCE_TOKENS:
            if token in content:
                violations.append(
                    f"Token legado/segredo no frontend: "
                    f"{relative_path}: {token}"
                )

    return violations


def check_frontend_bundle() -> list[str]:
    violations: list[str] = []

    if not DIST_ROOT.exists():
        violations.append(
            "frontend/dist não existe. Execute npm run build antes do guard."
        )
        return violations

    suffixes = {".js", ".css", ".html", ".map"}

    for path in DIST_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue

        content = read_text(path)

        for token in FORBIDDEN_BUNDLE_TOKENS:
            if token in content:
                violations.append(
                    f"Token server-side encontrado no bundle: "
                    f"{path.relative_to(REPO_ROOT)}: {token}"
                )

    return violations


def main() -> int:
    tracked = tracked_files()

    violations = []
    violations.extend(
        check_tracked_env_files(tracked)
    )
    violations.extend(
        check_frontend_source(tracked)
    )
    violations.extend(
        check_frontend_bundle()
    )

    if violations:
        print("RELEASE GUARD: FAILED")

        for violation in violations:
            print(f"- {violation}")

        return 1

    print("RELEASE GUARD: OK")
    print("- arquivos .env rastreados: OK")
    print("- frontend sem legado de credenciais: OK")
    print("- bundle sem segredo server-side: OK")

    return 0


if __name__ == "__main__":
    sys.exit(main())
