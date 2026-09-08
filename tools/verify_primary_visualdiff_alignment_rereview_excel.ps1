param(
    [Parameter(Mandatory = $true)][string]$WorkbookPath,
    [Parameter(Mandatory = $true)][string]$PayloadPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [int]$ExpectedRows = 254
)

$ErrorActionPreference = 'Stop'
$workbookFull = [System.IO.Path]::GetFullPath($WorkbookPath)
$payloadFull = [System.IO.Path]::GetFullPath($PayloadPath)
$outputFull = [System.IO.Path]::GetFullPath($OutputDir)
$reportFull = [System.IO.Path]::GetFullPath($ReportPath)
if (-not (Test-Path -LiteralPath $workbookFull -PathType Leaf)) {
    throw "Workbook does not exist: $workbookFull"
}
if (-not (Test-Path -LiteralPath $payloadFull -PathType Leaf)) {
    throw "Payload does not exist: $payloadFull"
}
$payload = Get-Content -LiteralPath $payloadFull -Raw | ConvertFrom-Json
if ($payload.rows.Count -ne $ExpectedRows) {
    throw "Payload row count mismatch: $($payload.rows.Count) != $ExpectedRows"
}
New-Item -ItemType Directory -Path $outputFull -Force | Out-Null
New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($reportFull)) -Force | Out-Null

function Export-RangePng {
    param($Worksheet, [string]$RangeAddress, [string]$OutputPath)
    $Worksheet.Activate()
    $range = $Worksheet.Range($RangeAddress)
    $range.Select() | Out-Null
    Start-Sleep -Milliseconds 200
    $range.CopyPicture(1, 2) | Out-Null
    $chartObject = $Worksheet.ChartObjects().Add(0, 0, $range.Width, $range.Height)
    $chart = $chartObject.Chart
    $chart.Paste() | Out-Null
    [void]$chart.Export($OutputPath, 'PNG')
    $chartObject.Delete()
    $Worksheet.Application.CutCopyMode = $false
    Start-Sleep -Milliseconds 150
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($chart) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($chartObject) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($range) | Out-Null
}

$excel = $null
$book = $null
$instructions = $null
$review = $null
$machine = $null
$stage = 'initialize'
try {
    $stage = 'open workbook'
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $true
    $book = $excel.Workbooks.Open($workbookFull, 0, $true)

    $stage = 'resolve worksheets'
    if ($book.Worksheets.Count -ne 3) {
        throw "Worksheet count mismatch: $($book.Worksheets.Count) != 3"
    }
    $instructions = $book.Worksheets.Item(1)
    $review = $book.Worksheets.Item(2)
    $machine = $book.Worksheets.Item(3)
    $startRow = 9
    $lastRow = $startRow + $ExpectedRows - 1

    $stage = 'validate image anchors'
    if ($review.Shapes.Count -ne $ExpectedRows) {
        throw "Evidence image count mismatch: $($review.Shapes.Count) != $ExpectedRows"
    }
    $shapeRows = New-Object System.Collections.Generic.List[int]
    for ($index = 1; $index -le $review.Shapes.Count; $index++) {
        $shape = $review.Shapes.Item($index)
        $shapeRows.Add([int]$shape.TopLeftCell.Row)
        if ([int]$shape.TopLeftCell.Column -ne 2) {
            throw "Evidence image $index is not anchored in column B"
        }
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shape) | Out-Null
    }
    $sortedRows = $shapeRows | Sort-Object
    for ($offset = 0; $offset -lt $ExpectedRows; $offset++) {
        if ($sortedRows[$offset] -ne ($startRow + $offset)) {
            throw "Missing or duplicate image anchor at review row $($startRow + $offset)"
        }
    }

    $stage = 'validate row cells and identities'
    $blankDecisions = 0
    $completionFormulas = 0
    $openStatuses = 0
    $machineIdentityMatches = 0
    $safeToMergeFalse = 0
    $initialStatus = [string]$review.Cells.Item($startRow, 10).Value2
    if ([string]::IsNullOrWhiteSpace($initialStatus)) {
        throw 'Initial completion status is blank'
    }
    for ($offset = 0; $offset -lt $ExpectedRows; $offset++) {
        $excelRow = $startRow + $offset
        if ([string]::IsNullOrWhiteSpace([string]$review.Cells.Item($excelRow, 5).Value2)) {
            $blankDecisions++
        }
        if ($review.Cells.Item($excelRow, 10).HasFormula) {
            $completionFormulas++
        }
        if ([string]$review.Cells.Item($excelRow, 10).Value2 -eq $initialStatus) {
            $openStatuses++
        }
        $machineRow = 2 + $offset
        if ([string]$machine.Cells.Item($machineRow, 3).Value2 -eq [string]$payload.rows[$offset].record_id) {
            $machineIdentityMatches++
        }
        if ($machine.Cells.Item($machineRow, 19).Value2 -eq $false) {
            $safeToMergeFalse++
        }
    }
    if ($blankDecisions -ne $ExpectedRows) {
        throw "Blank decision count mismatch: $blankDecisions != $ExpectedRows"
    }
    if ($completionFormulas -ne $ExpectedRows) {
        throw "Completion formula count mismatch: $completionFormulas != $ExpectedRows"
    }
    if ($openStatuses -ne $ExpectedRows) {
        throw "Initial open status count mismatch: $openStatuses != $ExpectedRows"
    }
    if ($machineIdentityMatches -ne $ExpectedRows) {
        throw "Machine identity count mismatch: $machineIdentityMatches != $ExpectedRows"
    }
    if ($safeToMergeFalse -ne $ExpectedRows) {
        throw "Fail-closed marker count mismatch: $safeToMergeFalse != $ExpectedRows"
    }

    $stage = 'validate dropdowns'
    foreach ($excelRow in @($startRow, $startRow + [math]::Floor($ExpectedRows / 2), $lastRow)) {
        if ([int]$review.Cells.Item($excelRow, 5).Validation.Type -ne 3) {
            throw "Decision validation is missing at E$excelRow"
        }
        if ([int]$review.Cells.Item($excelRow, 6).Validation.Type -ne 3) {
            throw "Change-type validation is missing at F$excelRow"
        }
    }

    $stage = 'export native Excel previews'
    $previewSpecs = @(
        @{ Sheet = $instructions; Range = 'A1:J22'; File = 'excel_00_instructions.png' },
        @{ Sheet = $review; Range = 'A1:J9'; File = 'excel_01_review_header.png' },
        @{ Sheet = $review; Range = 'A8:D10'; File = 'excel_02_review_start_evidence.png' },
        @{ Sheet = $review; Range = 'A135:D137'; File = 'excel_03_review_middle_evidence.png' },
        @{ Sheet = $review; Range = 'A260:D262'; File = 'excel_04_review_end_evidence.png' },
        @{ Sheet = $review; Range = 'E8:J10'; File = 'excel_05_review_inputs.png' },
        @{ Sheet = $machine; Range = 'A1:S8'; File = 'excel_06_machine_data.png' }
    )
    foreach ($spec in $previewSpecs) {
        Export-RangePng $spec.Sheet $spec.Range (Join-Path $outputFull $spec.File)
    }

    $stage = 'write native Excel report'
    $report = [ordered]@{
        status = 'PASS'
        goal = [string]$payload.goal
        workbook = $workbookFull
        workbook_sha256 = (Get-FileHash -LiteralPath $workbookFull -Algorithm SHA256).Hash.ToLowerInvariant()
        worksheet_count = [int]$book.Worksheets.Count
        rows = $ExpectedRows
        embedded_images = [int]$review.Shapes.Count
        first_image_row = [int]$sortedRows[0]
        last_image_row = [int]$sortedRows[-1]
        unique_image_rows = [int]($sortedRows | Select-Object -Unique).Count
        blank_decision_cells = $blankDecisions
        completion_formulas = $completionFormulas
        initial_open_statuses = $openStatuses
        machine_identity_matches = $machineIdentityMatches
        safe_to_merge_false = $safeToMergeFalse
        decision_validation_type = [int]$review.Cells.Item($startRow, 5).Validation.Type
        change_type_validation_type = [int]$review.Cells.Item($startRow, 6).Validation.Type
        output_png_count = [int](Get-ChildItem -LiteralPath $outputFull -Filter '*.png').Count
        safe_to_merge_gold = $false
        gold_rows_modified = 0
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportFull -Encoding UTF8
    $report | ConvertTo-Json -Depth 8

    $book.Close($false)
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($book) | Out-Null
    $book = $null
}
catch {
    throw "Native Excel verification failed during '$stage': $($_.Exception.Message)"
}
finally {
    foreach ($item in @($machine, $review, $instructions)) {
        if ($null -ne $item) {
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($item) | Out-Null
        }
    }
    if ($null -ne $book) {
        $book.Close($false)
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($book) | Out-Null
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
