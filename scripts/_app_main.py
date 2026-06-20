"""App entry used when launched from the .app bundle.

The bundle ships a *copy* of the framework Python binary inside
`Contents/MacOS/`, so that NSBundle.mainBundle() resolves to "Pysar.app"
(giving the Dock our name + icon natively instead of "Python"). That copy is a
bare interpreter with no venv context, so we splice the project's venv
site-packages in here — `addsitedir` also runs its .pth files, which is what
activates the editable `pysar` install.
"""

import os
import site

_sp = os.environ.get("PYSAR_SITE")
if _sp and os.path.isdir(_sp):
    site.addsitedir(_sp)

import runpy  # noqa: E402

runpy.run_module("pysar", run_name="__main__", alter_sys=True)
