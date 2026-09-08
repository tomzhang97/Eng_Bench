param(
    [Parameter(Mandatory = $true)]
    [string]$WorkbookDir,

    [Parameter(Mandatory = $true)]
    [string]$ReportJson,

    [string]$PreviewDir,

    [ValidateSet(12, 24)]
    [int]$RowsPerWorkbook = 12,

    [switch]$ReadOnly
)

$ErrorActionPreference = "Stop"
$resolvedWorkbookDir = (Resolve-Path -LiteralPath $WorkbookDir).Path
$resolvedReportJson = [System.IO.Path]::GetFullPath($ReportJson)
$resolvedPreviewDir = $null
if ($PreviewDir) {
    $resolvedPreviewDir = [System.IO.Path]::GetFullPath($PreviewDir)
    New-Item -ItemType Directory -Force -Path $resolvedPreviewDir | Out-Null
}

function Release-ComObject {
    param([object]$Object)
    if ($null -ne $Object -and [System.Runtime.InteropServices.Marshal]::IsComObject($Object)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Object)
    }
}

$excel = $null
$reports = @()
$issues = @()

try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false

    $workbooks = @(Get-ChildItem -LiteralPath $resolvedWorkbookDir -Filter "AUDITOR_*_REVIEW_$RowsPerWorkbook.xlsx" -File | Sort-Object Name)
    if ($workbooks.Count -eq 0) {
        throw "No auditor workbooks found in $resolvedWorkbookDir"
    }

    foreach ($file in $workbooks) {
        $book = $null
        $reviewSheet = $null
        $machineSheet = $null
        $range = $null
        $chartObject = $null
        $chart = $null
        $workbookIssues = @()
        $saved = $false
        $previewPath = $null
        $pictureCount = 0
        $blankDecisionCells = 0
        $completionText = ""
        $machineSheetVeryHidden = $false

        try {
            $book = $excel.Workbooks.Open($file.FullName, 0, [bool]$ReadOnly)
            if ($book.Worksheets.Count -ne 2) {
                $workbookIssues += "expected exactly 2 worksheets"
            }
            $reviewSheet = $book.Worksheets.Item(1)
            $machineSheet = $book.Worksheets.Item(2)
            if ($reviewSheet.Visible -ne -1) {
                $workbookIssues += "review sheet is not visible"
            }
            if (-not $ReadOnly -and $machineSheet.Visible -ne 2) {
                $machineSheet.Visible = 2
            }
            if ($machineSheet.Visible -ne 2) {
                $workbookIssues += "machine sheet is not veryHidden"
            }
            $machineSheetVeryHidden = $machineSheet.Visible -eq 2

            foreach ($shape in @($reviewSheet.Shapes)) {
                if ($shape.Type -eq 13) {
                    $pictureCount += 1
                }
            }
            if ($pictureCount -ne $RowsPerWorkbook) {
                $workbookIssues += "expected $RowsPerWorkbook embedded pictures, found $pictureCount"
            }

            $lastReviewRow = 6 + $RowsPerWorkbook
            foreach ($row in 7..$lastReviewRow) {
                $value = $reviewSheet.Cells.Item($row, 4).Value2
                if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
                    $blankDecisionCells += 1
                }
            }
            if ($blankDecisionCells -ne $RowsPerWorkbook) {
                $workbookIssues += "expected $RowsPerWorkbook blank decision cells, found $blankDecisionCells"
            }

            if (-not $ReadOnly) {
                $book.ForceFullCalculation = $true
                $excel.CalculateFullRebuild()
                $reviewSheet.Range("E5").Calculate()
            }
            $completionText = [string]$reviewSheet.Range("E5").Text
            $expectedCompletionText = "0 / $RowsPerWorkbook"
            if ($completionText -ne $expectedCompletionText) {
                $workbookIssues += "expected completion text '$expectedCompletionText', found '$completionText'"
            }

            $reviewSheet.Activate()
            $excel.ActiveWindow.Zoom = 85
            $reviewSheet.Range("D7").Select() | Out-Null

            if ($resolvedPreviewDir) {
                $range = $reviewSheet.Range("A1:E$lastReviewRow")
                $range.CopyPicture(1, 2)
                $chartObject = $reviewSheet.ChartObjects().Add(0, 0, $range.Width, $range.Height)
                $chart = $chartObject.Chart
                $chart.Paste()
                $previewPath = Join-Path $resolvedPreviewDir ($file.BaseName + ".png")
                [void]$chart.Export($previewPath, "PNG")
                $chartObject.Delete()
            }

            if (-not $ReadOnly) {
                $book.Save()
                $saved = $true
            }
        }
        catch {
            $workbookIssues += $_.Exception.Message
        }
        finally {
            if ($null -ne $book) {
                $book.Close($false)
            }
            Release-ComObject $chart
            Release-ComObject $chartObject
            Release-ComObject $range
            Release-ComObject $machineSheet
            Release-ComObject $reviewSheet
            Release-ComObject $book
        }

        $valid = $workbookIssues.Count -eq 0
        if (-not $valid) {
            $issues += @($workbookIssues | ForEach-Object { "$($file.Name): $_" })
        }
        $reports += [ordered]@{
            workbook = $file.Name
            embedded_pictures = $pictureCount
            blank_decision_cells = $blankDecisionCells
            completion_text = $completionText
            machine_sheet_very_hidden = $machineSheetVeryHidden
            saved = $saved
            read_only = [bool]$ReadOnly
            preview = $previewPath
            issues = $workbookIssues
            valid = $valid
        }
    }
}
finally {
    if ($null -ne $excel) {
        $excel.Quit()
    }
    Release-ComObject $excel
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$report = [ordered]@{
    goal = "Gold v2.0 Global"
    workflow = "native Excel auditor workbook verification"
    workbook_dir = $resolvedWorkbookDir
    workbook_count = $reports.Count
    expected_workbooks = $reports.Count
    rows_per_workbook = $RowsPerWorkbook
    reports = $reports
    issues = $issues
    gold_rows_modified = 0
    valid = ($reports.Count -gt 0 -and $issues.Count -eq 0)
}

$reportParent = Split-Path -Parent $resolvedReportJson
if ($reportParent) {
    New-Item -ItemType Directory -Force -Path $reportParent | Out-Null
}
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resolvedReportJson -Encoding UTF8
$report | ConvertTo-Json -Depth 8
if (-not $report.valid) {
    exit 1
}
