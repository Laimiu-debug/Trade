#!/usr/bin/env bash

set -euo pipefail

NAME="FinalTrade"
ICON_PATH=""
CLEAN=0
PYTHON_BIN="${PYTHON_BIN:-python3}"
NPM_BIN="${NPM_BIN:-npm}"

usage() {
  cat <<'EOF'
Usage:
  ./build-mac.sh [--name <AppName>] [--icon <icon.icns>] [--clean]

Options:
  --name    App bundle name. Default: FinalTrade
  --icon    Optional .icns icon file path.
  --clean   Remove previous build artifacts before packaging.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    --icon)
      ICON_PATH="$2"
      shift 2
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"
JOURNAL_FRONTEND_DIR="${REPO_ROOT}/journal-frontend"
BACKEND_VENV="${BACKEND_DIR}/.venv"
BACKEND_PYTHON="${BACKEND_VENV}/bin/python3"
FRONTEND_DIST="${FRONTEND_DIR}/dist"
JOURNAL_FRONTEND_DIST="${JOURNAL_FRONTEND_DIR}/dist"
BACKEND_DIST="${BACKEND_DIR}/dist"
BACKEND_BUILD="${BACKEND_DIR}/build"
REPO_DIST="${REPO_ROOT}/dist"
SPEC_FILE="${BACKEND_DIR}/${NAME}.spec"
DEFAULT_ICON_PATH="${REPO_ROOT}/assets/finaltrade.icns"
RESOLVED_ICON_PATH=""

if [[ -n "${ICON_PATH}" ]]; then
  if [[ ! -f "${ICON_PATH}" ]]; then
    echo "Icon file not found: ${ICON_PATH}" >&2
    exit 1
  fi
  RESOLVED_ICON_PATH="$(cd "$(dirname "${ICON_PATH}")" && pwd)/$(basename "${ICON_PATH}")"
elif [[ -f "${DEFAULT_ICON_PATH}" ]]; then
  RESOLVED_ICON_PATH="${DEFAULT_ICON_PATH}"
fi

if [[ ! -x "${BACKEND_PYTHON}" ]]; then
  echo "Creating backend virtual environment..."
  "${PYTHON_BIN}" -m venv "${BACKEND_VENV}"
fi

echo "Installing backend dependencies..."
"${BACKEND_PYTHON}" -m pip install --upgrade pip
"${BACKEND_PYTHON}" -m pip install -r "${BACKEND_DIR}/requirements.txt"
"${BACKEND_PYTHON}" -m pip install pyinstaller

echo "Building frontend..."
pushd "${FRONTEND_DIR}" >/dev/null
if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  "${NPM_BIN}" install
fi
"${NPM_BIN}" run build
popd >/dev/null

if [[ ! -f "${FRONTEND_DIST}/index.html" ]]; then
  echo "Frontend build failed: ${FRONTEND_DIST}/index.html not found" >&2
  exit 1
fi

echo "Building journal frontend..."
pushd "${JOURNAL_FRONTEND_DIR}" >/dev/null
if [[ ! -d "${JOURNAL_FRONTEND_DIR}/node_modules" ]]; then
  "${NPM_BIN}" install
fi
"${NPM_BIN}" run build
popd >/dev/null
if [[ ! -f "${JOURNAL_FRONTEND_DIST}/index.html" ]]; then
  echo "Journal frontend build failed: ${JOURNAL_FRONTEND_DIST}/index.html not found" >&2
  exit 1
fi

if [[ "${CLEAN}" -eq 1 ]]; then
  rm -rf "${BACKEND_DIST}" "${BACKEND_BUILD}" "${REPO_DIST}" "${SPEC_FILE}"
fi

echo "Packaging .app with PyInstaller..."
ADD_DATA_ARG="${FRONTEND_DIST}:frontend_dist"
JOURNAL_ADD_DATA_ARG="${JOURNAL_FRONTEND_DIST}:journal_frontend_dist"
PYI_ARGS=(
  "--noconfirm"
  "--clean"
  "--windowed"
  "--name" "${NAME}"
  "--add-data" "${ADD_DATA_ARG}"
  "--add-data" "${JOURNAL_ADD_DATA_ARG}"
  "--hidden-import" "uvicorn.loops.asyncio"
  "--hidden-import" "uvicorn.protocols.http.h11_impl"
  "--hidden-import" "uvicorn.lifespan.on"
  "desktop_launcher.py"
)

if [[ -n "${RESOLVED_ICON_PATH}" ]]; then
  PYI_ARGS+=("--icon" "${RESOLVED_ICON_PATH}")
fi

pushd "${BACKEND_DIR}" >/dev/null
"${BACKEND_PYTHON}" -m PyInstaller "${PYI_ARGS[@]}"
popd >/dev/null

APP_PATH="${BACKEND_DIST}/${NAME}.app"
if [[ ! -d "${APP_PATH}" ]]; then
  echo "Packaging failed: ${APP_PATH} not found" >&2
  exit 1
fi

mkdir -p "${REPO_DIST}"
TARGET_APP_PATH="${REPO_DIST}/${NAME}.app"
rm -rf "${TARGET_APP_PATH}"
cp -R "${APP_PATH}" "${TARGET_APP_PATH}"

ZIP_PATH="${REPO_DIST}/${NAME}-macOS.zip"
rm -f "${ZIP_PATH}"
ditto -c -k --sequesterRsrc --keepParent "${TARGET_APP_PATH}" "${ZIP_PATH}"

echo ""
echo "Build complete."
echo "App bundle: ${TARGET_APP_PATH}"
echo "Archive: ${ZIP_PATH}"
