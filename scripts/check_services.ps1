$ErrorActionPreference = "Continue"

$services = @(
  @{ Name = "n8n"; Url = "http://localhost:5782" },
  @{ Name = "FreshRSS"; Url = "http://localhost:18181" },
  @{ Name = "RSSHub"; Url = "http://localhost:1210" },
  @{ Name = "changedetection.io"; Url = "http://localhost:15100" },
  @{ Name = "Qdrant"; Url = "http://localhost:6433/healthz" },
  @{ Name = "analysis-worker"; Url = "http://localhost:18090/health" },
  @{ Name = "Instagram login helper"; Url = "http://localhost:18190/health" }
)

foreach ($service in $services) {
  try {
    $response = Invoke-WebRequest -Uri $service.Url -UseBasicParsing -TimeoutSec 10
    Write-Host ("OK   {0} {1}" -f $service.Name, $response.StatusCode)
  } catch {
    Write-Host ("FAIL {0} {1}" -f $service.Name, $_.Exception.Message)
  }
}
