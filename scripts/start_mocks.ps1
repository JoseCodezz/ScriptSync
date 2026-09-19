# Starts the two mock brand agents and the four attackers in the background.
# Run from the repo root with the venv active:  .\scripts\start_mocks.ps1
# Stop them all:  Get-Process python | Stop-Process   (careful: stops ALL python)
$py = "python"
Start-Process $py "-m dev.mock_agent --port 9001 --which A" -WindowStyle Hidden
Start-Process $py "-m dev.mock_agent --port 9002 --which B" -WindowStyle Hidden
Start-Process $py "-m dev.mock_agent --port 9101 --which A --mode lookalike --name a2a://labelAgent.drugInfo.BrandA.v1.0.0.brand-a5.example" -WindowStyle Hidden
Start-Process $py "-m dev.mock_agent --port 9102 --which A --mode expired --name a2a://labelAgent.drugInfo.BrandA.v1.1.0.brand-a.example" -WindowStyle Hidden
Start-Process $py "-m dev.mock_agent --port 9103 --which A --mode replay" -WindowStyle Hidden
Start-Process $py "-m dev.mock_agent --port 9104 --which A --mode revoked --name a2a://labelAgent.drugInfo.BrandA.v0.9.0.brand-a.example" -WindowStyle Hidden
Write-Host "Mock agents starting on ports 9001, 9002, 9101-9104"
