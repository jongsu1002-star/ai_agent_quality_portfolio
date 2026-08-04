param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [string]$VoiceName = 'Microsoft Heami Desktop'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $ManifestPath | ConvertFrom-Json
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $installed = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
    if ($VoiceName -notin $installed) {
        throw "Requested TTS voice is not installed: $VoiceName"
    }
    $synth.SelectVoice($VoiceName)
    $synth.Rate = 0
    $synth.Volume = 100

    foreach ($cue in $manifest) {
        $wavPath = [string]$cue.wav_path
        $parent = Split-Path -Parent $wavPath
        if ($parent) {
            [IO.Directory]::CreateDirectory($parent) | Out-Null
        }
        $synth.SetOutputToWaveFile($wavPath)
        try {
            $synth.Speak([string]$cue.text)
        }
        finally {
            $synth.SetOutputToNull()
        }
    }
}
finally {
    $synth.Dispose()
}
