# PyInstaller application. Run through scripts/build_trade_rebuild.py.
from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).resolve().parent
backend = root / 'backend'
package = backend / 'trade_app'
datas = [(str(root / 'frontend' / 'dist-rebuild'), 'frontend/dist-rebuild')]
hiddenimports = ['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
                 'uvicorn.protocols.websockets.websockets_impl', 'uvicorn.lifespan.on']
for path in package.rglob('*'):
    if path.is_file() and path.suffix in ('.py', '.json', '.sql'):
        datas.append((str(path), str(path.parent.relative_to(backend))))
        if path.suffix == '.py' and path.name != '__init__.py':
            hiddenimports.append('.'.join(path.with_suffix('').relative_to(backend).parts))
for name in ('tzdata', 'akshare', 'baostock', 'reportlab'):
    datas.extend(collect_data_files(name))

analysis = Analysis([str(root / 'scripts' / 'run_trade_rebuild.py')], pathex=[str(backend)],
    binaries=[], datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=['app', 'pytest', 'IPython', 'notebook', 'jupyterlab',
                                'torch', 'tensorflow', 'sklearn', 'matplotlib', 'PyQt5', 'PySide6'],
    noarchive=False)
archive = PYZ(analysis.pure)
application_icon = str(root / 'frontend' / 'dist-rebuild' / 'trade-icon.ico') if sys.platform == 'win32' else None
if os.environ.get('TRADE_REBUILD_BUNDLE_MODE') == 'onefile':
    executable = EXE(archive, analysis.scripts, analysis.binaries, analysis.datas, [],
        name='TradeRebuild', debug=False, bootloader_ignore_signals=False,
        strip=False, upx=False, console=True, icon=application_icon)
else:
    executable = EXE(archive, analysis.scripts, [], exclude_binaries=True, name='TradeRebuild',
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True, icon=application_icon)
    collection = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name='TradeRebuild')
    if sys.platform == 'darwin':
        bundle = BUNDLE(collection, name='TradeRebuild.app', bundle_identifier='io.laimiu.trade.rebuild',
            info_plist={'CFBundleShortVersionString': '0.1.0', 'NSHighResolutionCapable': True})
