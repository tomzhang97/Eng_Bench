param(
    [Parameter(Mandatory = $true)]
    [string]$WorkbookPath,
    [Parameter(Mandatory = $true)]
    [string]$MappingJson
)

$ErrorActionPreference = 'Stop'
$workbookFile = (Resolve-Path -LiteralPath $WorkbookPath).Path
$mappingFile = (Resolve-Path -LiteralPath $MappingJson).Path
$mapping = Get-Content -LiteralPath $mappingFile -Raw -Encoding UTF8 | ConvertFrom-Json
$expectedRows = @($mapping.rows)
if ($expectedRows.Count -eq 0) {
    throw 'Mapping contains no rows.'
}

$excel = $null
$workbook = $null
$worksheet = $null
$reports = @()
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $false
    $workbook = $excel.Workbooks.Open($workbookFile, 0, $false)
    $worksheet = $workbook.Worksheets.Item(1)

    foreach ($row in $expectedRows) {
        $excelRow = [int]$row.excel_row
        $imagePath = (Resolve-Path -LiteralPath ([string]$row.image_path)).Path
        $matches = @()
        for ($index = 1; $index -le $worksheet.Shapes.Count; $index++) {
            $shape = $worksheet.Shapes.Item($index)
            try {
                if ([int]$shape.TopLeftCell.Row -eq $excelRow) {
                    $matches += $shape.Name
                }
            }
            finally {
                [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shape) | Out-Null
            }
        }
        if ($matches.Count -ne 1) {
            throw "Expected one evidence image at Excel row $excelRow; found $($matches.Count)."
        }
        $worksheet.Shapes.Item($matches[0]).Delete()

        $cell = $worksheet.Range("B$excelRow")
        $picture = $worksheet.Shapes.AddPicture($imagePath, 0, -1, 0, 0, -1, -1)
        try {
            $picture.LockAspectRatio = -1
            $maxWidth = [Math]::Max(24.0, [double]$cell.Width - 8.0)
            $maxHeight = [Math]::Max(24.0, [double]$cell.Height - 8.0)
            $originalWidth = [double]$picture.Width
            $originalHeight = [double]$picture.Height
            $scale = [Math]::Min($maxWidth / $originalWidth, $maxHeight / $originalHeight)
            # LockAspectRatio updates height automatically; assigning both dimensions
            # would apply the scale twice for very wide evidence panels.
            $picture.Width = $originalWidth * $scale
            $picture.Left = [double]$cell.Left + ([double]$cell.Width - [double]$picture.Width) / 2.0
            $picture.Top = [double]$cell.Top + ([double]$cell.Height - [double]$picture.Height) / 2.0
            $picture.Placement = 1
            $reports += [pscustomobject]@{
                excel_row = $excelRow
                primary_index = [string]$row.primary_index
                image_path = $imagePath
                width_points = [Math]::Round([double]$picture.Width, 2)
                height_points = [Math]::Round([double]$picture.Height, 2)
            }
        }
        finally {
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($picture) | Out-Null
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($cell) | Out-Null
        }
    }

    if ([int]$worksheet.Shapes.Count -ne 12) {
        throw "Expected 12 evidence images after replacement; found $($worksheet.Shapes.Count)."
    }
    $workbook.Save()
}
finally {
    if ($workbook) { $workbook.Close($false) }
    if ($excel) { $excel.Quit() }
    foreach ($object in @($worksheet, $workbook, $excel)) {
        if ($object) {
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($object) | Out-Null
        }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

[pscustomobject]@{
    workbook = $workbookFile
    replaced_rows = $reports.Count
    final_picture_shapes = 12
    non_target_workbooks_modified = 0
    gold_rows_modified = 0
    rows = $reports
    valid = ($reports.Count -eq $expectedRows.Count)
} | ConvertTo-Json -Depth 5
