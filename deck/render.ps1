param([string]$Deck, [string]$OutDir, [int]$Width = 1600)
# Render every slide of a .pptx to PNG with PowerPoint (visual QA on Windows, where LibreOffice is absent).
$Deck = (Resolve-Path $Deck).Path
New-Item -ItemType Directory -Force $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
Get-ChildItem $OutDir -Filter "slide-*.png" | Remove-Item
$app = New-Object -ComObject PowerPoint.Application
try {
    $pres = $app.Presentations.Open($Deck, $true, $false, $false)  # read-only, untitled, no window
    $ratio = $pres.PageSetup.SlideHeight / $pres.PageSetup.SlideWidth
    foreach ($s in $pres.Slides) {
        $path = Join-Path $OutDir ("slide-{0}.png" -f $s.SlideIndex)
        $s.Export($path, "PNG", $Width, [int]($Width * $ratio))
        $path
    }
    $pres.Close()
} finally {
    $app.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
}
