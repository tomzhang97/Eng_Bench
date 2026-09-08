param(
    [Parameter(Mandatory = $true)][string]$WorkbookPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [Parameter(Mandatory = $true)][int]$ExpectedMicro,
    [Parameter(Mandatory = $true)][int]$ExpectedVisual,
    [Parameter(Mandatory = $true)][int]$ExpectedEngineering
)

$ErrorActionPreference = 'Stop'
$workbookFull = [System.IO.Path]::GetFullPath($WorkbookPath)
$outputFull = [System.IO.Path]::GetFullPath($OutputDir)
$reportFull = [System.IO.Path]::GetFullPath($ReportPath)
if (-not (Test-Path -LiteralPath $workbookFull -PathType Leaf)) {
    throw "Workbook does not exist: $workbookFull"
}
New-Item -ItemType Directory -Path $outputFull -Force | Out-Null
New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($reportFull)) -Force | Out-Null

function Export-RangePng {
    param($Worksheet, [string]$RangeAddress, [string]$OutputPath)
    $Worksheet.Activate()
    $range = $Worksheet.Range($RangeAddress)
    $range.Select() | Out-Null
    Start-Sleep -Milliseconds 250
    $range.CopyPicture(1, 2) | Out-Null
    $chartObject = $Worksheet.ChartObjects().Add(0, 0, $range.Width, $range.Height)
    $chart = $chartObject.Chart
    $chart.Paste() | Out-Null
    [void]$chart.Export($OutputPath, 'PNG')
    $chartObject.Delete()
    $Worksheet.Application.CutCopyMode = $false
    Start-Sleep -Milliseconds 200
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($chart) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($chartObject) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($range) | Out-Null
}

$excel = $null
$book = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $true
    $book = $excel.Workbooks.Open($workbookFull, 0, $true)

    $expected = @(
        @{ Index = 1; Range = 'A1:J30'; File = 'excel_00_instructions.png' },
        @{ Index = 2; Range = 'A1:J10'; File = 'excel_01_microtext_start.png' },
        @{ Index = 3; Range = 'A1:J9'; File = 'excel_03_visualdiff_start.png' },
        @{ Index = 4; Range = 'A1:H12'; File = 'excel_05_engineering_start.png' },
        @{ Index = 5; Range = 'A1:O7'; File = 'excel_07_machine_start.png' }
    )
    $sheets = @()
    foreach ($spec in $expected) {
        $sheet = $book.Worksheets.Item($spec.Index)
        Write-Output ("Rendering " + $sheet.Name + "!" + $spec.Range)
        Export-RangePng $sheet $spec.Range (Join-Path $outputFull $spec.File)
        Write-Output ("Rendered " + $spec.File)
        $sheets += [ordered]@{
            name = $sheet.Name
            used_rows = $sheet.UsedRange.Rows.Count
            used_columns = $sheet.UsedRange.Columns.Count
            shapes = $sheet.Shapes.Count
        }
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($sheet) | Out-Null
    }

    $micro = $book.Worksheets.Item(2)
    $visual = $book.Worksheets.Item(3)
    $engineering = $book.Worksheets.Item(4)
    if ($micro.Shapes.Count -ne $ExpectedMicro) {
        throw "MicroText image count mismatch: $($micro.Shapes.Count) != $ExpectedMicro"
    }
    if ($visual.Shapes.Count -ne $ExpectedVisual) {
        throw "VisualDiff image count mismatch: $($visual.Shapes.Count) != $ExpectedVisual"
    }
    if ($engineering.UsedRange.Rows.Count -ne ($ExpectedEngineering + 6)) {
        throw "Engineering row count mismatch: $($engineering.UsedRange.Rows.Count) != $($ExpectedEngineering + 6)"
    }

    $report = [ordered]@{
        status = 'PASS'
        workbook = $workbookFull
        worksheet_count = $book.Worksheets.Count
        sheets = $sheets
        total_shapes = ($sheets | ForEach-Object { $_['shapes'] } | Measure-Object -Sum).Sum
        micro_decision_validation_type = $micro.Range('E7').Validation.Type
        micro_category_validation_type = $micro.Range('G7').Validation.Type
        visual_decision_validation_type = $visual.Range('E7').Validation.Type
        visual_change_validation_type = $visual.Range('F7').Validation.Type
        engineering_locator = $engineering.Range('G7').Value2
        engineering_status_formula = $engineering.Range('H7').Formula
        micro_status_formula = $micro.Range('J7').Formula
        visual_status_formula = $visual.Range('J7').Formula
        micro_first_shape_row = $micro.Shapes.Item(1).TopLeftCell.Row
        micro_last_shape_row = $micro.Shapes.Item($micro.Shapes.Count).TopLeftCell.Row
        visual_first_shape_row = $visual.Shapes.Item(1).TopLeftCell.Row
        visual_last_shape_row = $visual.Shapes.Item($visual.Shapes.Count).TopLeftCell.Row
        expected_micro_rows = $ExpectedMicro
        expected_visual_rows = $ExpectedVisual
        expected_engineering_rows = $ExpectedEngineering
        output_png_count = (Get-ChildItem -LiteralPath $outputFull -Filter '*.png').Count
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportFull -Encoding UTF8
    $report | ConvertTo-Json -Depth 8

    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($micro) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($visual) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($engineering) | Out-Null
    $book.Close($false)
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($book) | Out-Null
    $book = $null
}
finally {
    if ($book) {
        $book.Close($false)
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($book) | Out-Null
    }
    if ($excel) {
        $excel.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
