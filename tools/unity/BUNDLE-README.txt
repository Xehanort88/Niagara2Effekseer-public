Unity FX tools (Unity game A, Unity game B)
==================================================================================

Unzip anywhere, e.g. D:\UnityFX-tools\. Two sides, both from tools/unity in the Niagara2Effekseer
repository (VERSION.txt names the commit; repack with tools/unity/pack.py rather than editing these copies).

Needs: Python 3.9+ with numpy and Pillow; UnityPy (pip install UnityPy) for the bundle side;
RenderDoc 1.46 for the capture side (default <RenderDoc folder>,
else add --renderdoc <folder>); ffmpeg on PATH for videos (else add --ffmpeg <exe>, or pip install imageio-ffmpeg).


1. Bundle side: the game's particle prefabs (Shuriken), unityfx.ps1
---------------------------------------------------------------------
  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game RT
  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game WOTR
  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game RT   -Container "plasma"
  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game WOTR -Container "fireball" -Bundle "<bundle column>"
Results under D:\UnityFX-out\rt or \wotr (-Out to change): the FX index (Unity game B), the bundle index,
the prefab list, and with -Container the prefab dumps and their summaries. Steps already done are skipped.


2. Capture side: what the game drew, run_rdc.bat
--------------------------------------------------
  run_rdc.bat "C:\Program Files (x86)\Steam\steamapps\common\Unity Game A\Renderdoc\breatheffect"
  run_rdc.bat "C:\Program Files (x86)\Steam\steamapps\common\Unity Game A\Renderdoc"           (every effect folder)
  run_rdc.bat "C:\Program Files (x86)\Steam\steamapps\common\Unity Game B\Renderdpc"
For each capture folder (one that holds .rdc files) it:
  1. cuts every video into frames-1fps-<video>\ beside it (one PNG per second, named by the second),
  2. digests every .rdc into <folder>\rdcdigest\ (the .rdc files are only read; the first run takes
     minutes per capture, later runs skip captures already digested),
  3. writes the analysis into <folder's parent>\_analysis\<wotr|rt>-<folder>-<date>\: REPORT.md,
     view.html (open it in a browser), layers.json, and the textures, contribution images, particle
     CSVs and shaders. Unity keeps textures and render targets upside down on D3D (RenderDoc shows them
     so); the analysis turns them upright (--no-flip keeps them as stored). Layers are ranked by the
     light they add to the scene colour target; masks and glow buffers are listed apart with the passes
     that read them, and UI draws are left out (--ui-re).
An _analysis folder that already exists is refused; add --overwrite to redo it:
  run_rdc.bat "<capture folder>" --skip-digest --overwrite
Options: python unity_rdc.py --help.
