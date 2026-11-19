# Create a free self-signed code signing certificate for local testing
param(
    [string]$Subject = "CN=SandeepMopuri",
    [string]$PfxPath = "dotix_test.pfx",
    [string]$Password = "dotix123"
)

Write-Host "Creating self-signed code signing certificate for $Subject..."
$secPassword = ConvertTo-SecureString -String $Password -Force -AsPlainText

$cert = New-SelfSignedCertificate `
    -Type Custom `
    -Subject $Subject `
    -KeyUsage DigitalSignature `
    -FriendlyName "dotix Local Test Certificate" `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3")

Export-PfxCertificate -Cert $cert -FilePath $PfxPath -Password $secPassword | Out-Null

Write-Host "Certificate exported to: $PfxPath"
Write-Host "To trust this certificate on your PC for local MSIX installation, run:"
Write-Host "  Import-Certificate -FilePath $PfxPath -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
