<#
Runs the Unity FX tools for Unity game B or Unity game A, step by step, from the folder this script is
saved in (the five .py files must be beside it). Every result goes under -Out (default D:\UnityFX-out\rt or \wotr).
A step whose output already exists is skipped, so the script can be run again after an interruption.

  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game RT
  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game WOTR
  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game RT   -Container "plasma"
  powershell -ExecutionPolicy Bypass -File unityfx.ps1 -Game WOTR -Container "fireball" -Bundle "<bundle column>"

Without -Container: Unity game B's FX index (fx.csv), the bundle index and the prefab list (list\containers.csv).
With -Container: also dumps the prefabs whose container path holds that text and summarises them
(dump-<text>\, summary-<text>\). Unity game B's bundle defaults to FX; for Unity game A give -Bundle from the list.
-GameRoot overrides the install folder; -Python names another interpreter (e.g. py).
#>
param(
    [ValidateSet('RT', 'WOTR')][string]$Game = 'RT',
    [string]$Out = 'D:\UnityFX-out',
    [string]$Container = '',
    [string]$Bundle = '',
    [string]$Python = 'python',
    [string]$GameRoot = ''
)

$Tools = $PSScriptRoot
if ($GameRoot -ne '') {
    $Root = $GameRoot
} elseif ($Game -eq 'RT') {
    $Root = 'C:\Program Files (x86)\Steam\steamapps\common\Unity Game B'
} else {
    $Root = 'C:\Program Files (x86)\Steam\steamapps\common\Unity Game A'
}
$Bundles = Join-Path $Root 'Bundles'
$Work = Join-Path $Out $Game.ToLower()
New-Item -ItemType Directory -Force -Path $Work | Out-Null

function Test-Done([string]$Path) {
    # a file that exists, or a folder holding anything, counts as a finished step
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return $true }
    return [bool](Get-ChildItem -LiteralPath $Path -Force | Select-Object -First 1)
}

function Invoke-Step([string]$Name, [string]$Result, [string[]]$Arguments) {
    if (Test-Done $Result) {
        Write-Host "== $Name : already done ($Result), skipped. Delete it to run the step again."
        return $true
    }
    Write-Host "== $Name"
    & $Python @Arguments | Out-Host          # shown, not returned: the function returns only true/false
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   $Name ended with exit code $LASTEXITCODE (see the lines above)."
        return $false
    }
    return $true
}

foreach ($f in 'fx_reference_index.py', 'unity_dump.py', 'shuriken_summary.py', 'shuriken.py', 'unityasset.py') {
    if (-not (Test-Path -LiteralPath (Join-Path $Tools $f))) {
        Write-Host "Missing $f beside this script ($Tools)."
        exit 2
    }
}
if (-not (Test-Path -LiteralPath $Bundles)) {
    Write-Host "No Bundles folder at $Bundles"
    exit 2
}

$Index = Join-Path $Work 'index'
$List = Join-Path $Work 'list'
$ok = $true
if ($Game -eq 'RT') {
    $ok = Invoke-Step 'FX references (fx.csv)' (Join-Path $Work 'fx.csv') @(
        (Join-Path $Tools 'fx_reference_index.py'), '--game', $Root, '--out', (Join-Path $Work 'fx.csv'),
        '--save-cache', (Join-Path $Work 'cache.json'))
}
$ok = (Invoke-Step 'bundle index (index\cab_index.csv)' $Index @(
    (Join-Path $Tools 'unity_dump.py'), '--bundles', $Bundles, '--out', $Index)) -and $ok
if ($Game -eq 'RT') {
    $ListInputs = @((Join-Path $Bundles 'FX'))
} else {
    $ListInputs = @((Join-Path $Bundles 'FX*'))     # the pattern with its folder: unity_dump matches it, ignoring case
}
$ok = (Invoke-Step 'prefab list (list\containers.csv)' $List (
    @((Join-Path $Tools 'unity_dump.py')) + $ListInputs + @('--out', $List, '--list'))) -and $ok

if ($Container -ne '') {
    if ($Bundle -eq '') {
        if ($Game -eq 'RT') {
            $Bundle = 'FX'
        } else {
            Write-Host "Unity game A has many FX bundles: give -Bundle with the 'bundle' column of $List\containers.csv"
            exit 2
        }
    }
    $Tag = $Container -replace '[^A-Za-z0-9_-]', '_'
    $Dump = Join-Path $Work "dump-$Tag"
    $Summary = Join-Path $Work "summary-$Tag"
    $ok = (Invoke-Step "dump prefabs matching '$Container'" $Dump @(
        (Join-Path $Tools 'unity_dump.py'), (Join-Path $Bundles $Bundle), '--bundles', $Bundles,
        '--cab-index', (Join-Path $Index 'cab_index.csv'), '--out', $Dump, '--container', $Container,
        '--export-textures')) -and $ok
    if (Test-Done $Dump) {
        $ok = (Invoke-Step 'summaries' $Summary @(
            (Join-Path $Tools 'shuriken_summary.py'), $Dump, '--out', $Summary)) -and $ok
    }
}

Write-Host ''
Write-Host "Results: $Work"
if (-not $ok) {
    Write-Host 'One or more steps reported a problem: see the lines above.'
    exit 1
}
