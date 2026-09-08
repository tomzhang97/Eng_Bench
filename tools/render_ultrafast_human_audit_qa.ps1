param(
    [Parameter(Mandatory = $true)][string]$WorkbookDir,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = 'Stop'
$workbooks = [System.IO.Path]::GetFullPath($WorkbookDir)
$output = [System.IO.Path]::GetFullPath($OutputDir)
if (-not (Test-Path -LiteralPath $workbooks -PathType Container)) {
    throw "Workbook directory does not exist: $workbooks"
}
New-Item -ItemType Directory -Path $output -Force | Out-Null

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
    $chart.Export($OutputPath, 'PNG') | Out-Null
    $chartObject.Delete()
    $chart = $null
    $chartObject = $null
    $range = $null
}

$excel = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $true

    $primaryPath = Join-Path $workbooks 'PRIMARY_REVIEW_498.xlsx'
    $primary = $excel.Workbooks.Open($primaryPath, 0, $true)
    $primarySheet = $primary.Worksheets.Item(1)
    Export-RangePng $primarySheet 'A1:G12' (Join-Path $output 'primary_start.png')
    Export-RangePng $primarySheet 'A372:G380' (Join-Path $output 'primary_transition.png')
    Export-RangePng $primarySheet 'A499:G504' (Join-Path $output 'primary_end.png')
    $primary.Close($false)
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($primarySheet) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($primary) | Out-Null

    1..10 | ForEach-Object {
        $number = $_.ToString('00')
        $path = Join-Path $workbooks "AUDITOR_${number}_REVIEW_12.xlsx"
        $book = $excel.Workbooks.Open($path, 0, $true)
        $sheet = $book.Worksheets.Item(1)
        Export-RangePng $sheet 'A1:E18' (Join-Path $output "auditor_${number}.png")
        $book.Close($false)
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($sheet) | Out-Null
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($book) | Out-Null
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
