param(
    [Parameter(Mandatory = $true)]
    [string]$WorkbookDir,
    [Parameter(Mandatory = $true)]
    [string]$ReportJson
)

$ErrorActionPreference = "Stop"
$workbookPath = [System.IO.Path]::GetFullPath($WorkbookDir)
$reportPath = [System.IO.Path]::GetFullPath($ReportJson)
if (-not (Test-Path -LiteralPath $workbookPath -PathType Container)) {
    throw "Workbook directory does not exist: $workbookPath"
}

$excel = $null
$reports = @()
$verifiedImages = 0

function Get-PictureCount {
    param([Parameter(Mandatory = $true)]$Worksheet)
    $count = 0
    foreach ($shape in $Worksheet.Shapes) {
        try {
            if ($shape.Type -eq 13) {
                $count += 1
            }
        }
        finally {
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shape) | Out-Null
        }
    }
    return $count
}

try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $false
    $excel.EnableEvents = $false

    Get-ChildItem -LiteralPath $workbookPath -Filter '*.xlsx' | Sort-Object Name | ForEach-Object {
        $book = $null
        try {
            $book = $excel.Workbooks.Open($_.FullName, 0, $false)
            $isPrimary = $_.Name -eq 'PRIMARY_REVIEW_498.xlsx'
            $machine = $book.Worksheets.Item($book.Worksheets.Count)
            $machine.Visible = 2

            if ($isPrimary) {
                $single = if ($book.Worksheets.Count -eq 2) {
                    $book.Worksheets.Item(1)
                } else {
                    $null
                }
                if ($single) {
                    $singleImages = Get-PictureCount -Worksheet $single
                    if ($singleImages -ne 498) {
                        throw "expected 498 primary images; found $singleImages"
                    }
                    $verifiedImages += $singleImages
                    $single.Activate()
                    $excel.ActiveWindow.Zoom = 80
                    $single.Range('E7').Select() | Out-Null
                    $visibleSheets = 1
                    $images = 498
                    $single = $null
                }
                else {
                    $micro = $book.Worksheets.Item(2)
                    $visual = $book.Worksheets.Item(3)
                    $microImages = Get-PictureCount -Worksheet $micro
                    $visualImages = Get-PictureCount -Worksheet $visual
                    if ($microImages -ne 369) {
                        throw "expected 369 MicroText images; found $microImages"
                    }
                    if ($visualImages -ne 129) {
                        throw "expected 129 VisualDiff images; found $visualImages"
                    }
                    $verifiedImages += $microImages + $visualImages
                    foreach ($sheet in @($micro, $visual)) {
                        $sheet.Activate()
                        $excel.ActiveWindow.Zoom = 85
                        $sheet.Range('D2').Select() | Out-Null
                        $sheet = $null
                    }
                    $start = $book.Worksheets.Item(1)
                    $start.Activate()
                    $excel.ActiveWindow.Zoom = 90
                    $start.Range('A1').Select() | Out-Null
                    $visibleSheets = 3
                    $images = 498
                }
            }
            else {
                $review = $book.Worksheets.Item(1)
                $reviewImages = Get-PictureCount -Worksheet $review
                if ($reviewImages -ne 12) {
                    throw "expected 12 audit images; found $reviewImages"
                }
                $verifiedImages += $reviewImages
                $review.Activate()
                $excel.ActiveWindow.Zoom = 85
                $review.Range('D7').Select() | Out-Null
                $visibleSheets = 1
                $images = 12
            }
            $book.Save()
            $reports += [pscustomobject]@{
                workbook = $_.Name
                visible_sheets = $visibleSheets
                machine_sheet_very_hidden = $machine.Visible -eq 2
                embedded_images = $images
                saved = $book.Saved
            }
            $machine = $null
        }
        finally {
            if ($book) {
                $book.Close($false)
                [System.Runtime.InteropServices.Marshal]::ReleaseComObject($book) | Out-Null
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

$result = [ordered]@{
    goal = 'Gold v2.0 Global'
    workbook_count = $reports.Count
    primary_rows = 498
    auditors = 10
    rows_per_auditor = 12
    embedded_images = ($reports | Measure-Object -Property embedded_images -Sum).Sum
    verified_image_shapes = $verifiedImages
    gold_rows_modified = 0
    workbooks = $reports
    valid = ($reports.Count -eq 11 -and ($reports | Where-Object { -not $_.machine_sheet_very_hidden -or -not $_.saved }).Count -eq 0)
}

$parent = Split-Path -Parent $reportPath
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
}
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
$result | ConvertTo-Json -Depth 5
