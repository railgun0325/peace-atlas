$ErrorActionPreference = "Continue"

$services = @(
  @{ Name = "n8n"; Url = "http://localhost:5678" },
  @{ Name = "FreshRSS"; Url = "http://localhost:8081" },
  @{ Name = "RSSHub"; Url = "http://localhost:1200" },
  @{ Name = "changedetection.io"; Url = "http://localhost:5000" },
  @{ Name = "Qdrant"; Url = "http://localhost:6333/healthz" },
  @{ Name = "analysis-worker"; Url = "http://localhost:8090/health" }
)

foreach ($service in $services) {
  try {
    $response = Invoke-WebRequest -Uri $service.Url -UseBasicParsing -TimeoutSec 10
    Write-Host ("OK   {0} {1}" -f $service.Name, $response.StatusCode)
  } catch {
    Write-Host ("FAIL {0} {1}" -f $service.Name, $_.Exception.Message)
  }
}
