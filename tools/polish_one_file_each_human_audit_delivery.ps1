param(
    [Parameter(Mandatory = $true)]
    [string]$DeliveryDir
)

$ErrorActionPreference = "Stop"
$deliveryPath = [System.IO.Path]::GetFullPath($DeliveryDir)
if (-not (Test-Path -LiteralPath $deliveryPath -PathType Container)) {
    throw "Delivery directory does not exist: $deliveryPath"
}

$excel = $null
$reports = @()
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $false

    Get-ChildItem -LiteralPath $deliveryPath -Filter '*.xlsx' | Sort-Object Name | ForEach-Object {
        $currentName = $_.Name
        $workbook = $null
        $resized = 0
        $imageCount = 0
        $sheetIndex = 0
        $shapeIndex = 0
        $shapeCount = 0
        try {
            $workbook = $excel.Workbooks.Open($_.FullName, 0, $false)
            $isPrimary = $_.Name -eq 'PRIMARY_REVIEW_498.xlsx'
            foreach ($sheetIndex in 2, 3) {
                $worksheet = $workbook.Worksheets.Item($sheetIndex)
                $worksheet.Activate()
                $window = $excel.ActiveWindow
                $window.FreezePanes = $false
                $window.SplitColumn = 0
                $window.SplitRow = 1
                $window.FreezePanes = $true
                $window.Zoom = if ($isPrimary) { 85 } else { 100 }

                if ($isPrimary -and $sheetIndex -eq 2) {
                    $worksheet.Range('H:J').EntireColumn.Hidden = $true
                }
                elseif ($isPrimary -and $sheetIndex -eq 3) {
                    $worksheet.Range('G:J').EntireColumn.Hidden = $true
                }
                else {
                    $worksheet.Range('F:J').EntireColumn.Hidden = $true
                }

                $shapeCount = $worksheet.Shapes.Count
                for ($shapeIndex = 1; $shapeIndex -le $shapeCount; $shapeIndex += 1) {
                    $shape = $worksheet.Shapes.Item($shapeIndex)
                    if ($shape.Type -ne 13) {
                        $shape = $null
                        continue
                    }
                    $imageCount += 1
                    $row = $shape.TopLeftCell.Row
                    $cell = $worksheet.Cells.Item($row, 2)
                    $shape.Placement = 3
                    $maxWidth = [Math]::Max(20.0, $cell.Width - 8.0)
                    $maxHeight = [Math]::Max(20.0, $cell.Height - 12.0)
                    $ratio = [Math]::Min(
                        1.0,
                        [Math]::Min($maxWidth / $shape.Width, $maxHeight / $shape.Height)
                    )
                    if ($ratio -lt 0.999) {
                        $shape.LockAspectRatio = -1
                        $shape.Width = $shape.Width * $ratio
                        $resized += 1
                    }
                    $shape.Left = $cell.Left + (($cell.Width - $shape.Width) / 2.0)
                    $shape.Top = $cell.Top + (($cell.Height - $shape.Height) / 2.0)
                    $shape = $null
                    $cell = $null
                }
                $worksheet.Range('D2').Select() | Out-Null
                $worksheet = $null
            }
            $workbook.Worksheets.Item(1).Activate()
            $workbook.Save()
            $reports += [pscustomobject]@{
                workbook = $_.Name
                embedded_images = $imageCount
                resized_images = $resized
                saved = $workbook.Saved
            }
        }
        catch {
            throw "Failed to polish $currentName at sheet $sheetIndex, shape $shapeIndex/$shapeCount, image $imageCount (line $($_.InvocationInfo.ScriptLineNumber)): $($_.Exception.Message)"
        }
        finally {
            if ($workbook) {
                $workbook.Close($false)
                [System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook) | Out-Null
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
