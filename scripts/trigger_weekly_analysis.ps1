param(
  [string]$StartDate = "",
  [string]$EndDate = ""
)

if (-not $EndDate) {
  $EndDate = (Get-Date).ToString("yyyy-MM-dd")
}
if (-not $StartDate) {
  $StartDate = (Get-Date).AddDays(-7).ToString("yyyy-MM-dd")
}

$body = @{
  start_date = $StartDate
  end_date = $EndDate
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8090/analyze/weekly" `
  -ContentType "application/json" `
  -Body $body
