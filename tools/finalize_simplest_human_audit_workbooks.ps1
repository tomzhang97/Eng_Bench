param(
    [Parameter(Mandatory = $true)]
    [string]$WorkbookDir,
    [ValidateSet('D', 'E')]
    [string]$PrimaryDecisionColumn = 'E'
)

$ErrorActionPreference = 'Stop'
$resolvedDir = (Resolve-Path -LiteralPath $WorkbookDir).Path
$expected = @('PRIMARY_REVIEW_498.xlsx') + @(
    1..10 | ForEach-Object { 'AUDITOR_{0:D2}_REVIEW_12.xlsx' -f $_ }
)

foreach ($name in $expected) {
    $path = Join-Path $resolvedDir $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing workbook: $path"
    }
}

$excel = $null
$reports = @()
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $false

    foreach ($name in $expected) {
        $path = Join-Path $resolvedDir $name
        $workbook = $null
        $worksheet = $null
        $window = $null
        $target = $null
        try {
            $workbook = $excel.Workbooks.Open($path, 0, $false)
            $worksheet = $workbook.Worksheets.Item(1)
            $worksheet.Activate()
            $window = $workbook.Windows.Item(1)

            $window.FreezePanes = $false
            $window.SplitColumn = 0
            $window.SplitRow = 0
            $worksheet.Range('B7').Select() | Out-Null
            $window.FreezePanes = $true

            $isPrimary = $name -eq 'PRIMARY_REVIEW_498.xlsx'
            $target = if ($isPrimary) {
                $worksheet.Range("${PrimaryDecisionColumn}7")
            } else {
                $worksheet.Range('D7')
            }
            $target.Select() | Out-Null
            $window.Zoom = if ($isPrimary) { 80 } else { 85 }
            $window.ScrollColumn = 1
            $window.ScrollRow = 7
            $window.DisplayGridlines = $false

            $workbook.Save()
            $reports += [pscustomobject]@{
                workbook = $name
                sheet = $worksheet.Name
                active_cell = $excel.ActiveCell.Address()
                frozen = [bool]$window.FreezePanes
                split_rows = [int]$window.SplitRow
                split_columns = [int]$window.SplitColumn
                zoom = [int]$window.Zoom
                picture_shapes = [int]$worksheet.Shapes.Count
                saved = [bool]$workbook.Saved
            }
        }
        finally {
            if ($workbook) {
                $workbook.Close($false)
            }
            foreach ($object in @($target, $window, $worksheet, $workbook)) {
                if ($object) {
                    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($object) | Out-Null
                }
            }
        }
    }
}
finally {
    if ($excel) {
        $excel.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$reports | ConvertTo-Json -Depth 3
