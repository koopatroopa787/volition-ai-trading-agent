param(
    [Parameter(Mandatory = $true)]
    [string]$FfmpegExe
)

$ErrorActionPreference = "Stop"
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$docsDir = $PSScriptRoot
$assetsDir = Join-Path $docsDir "video-assets"
$buildDir = Join-Path $docsDir "video-build"
$outputFile = Join-Path $docsDir "Volition_Demo_Walkthrough.mp4"

if (-not (Test-Path -LiteralPath $FfmpegExe)) {
    throw "FFmpeg was not found at $FfmpegExe"
}

New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$scenes = @(
    @{
        Image = Join-Path $docsDir "Volition_Hackathon_Cover.png"
        Narration = "Meet Volition: the autonomous options desk that knows when not to trade. It was built for the Alpaca AI Trading Agents Hackathon around one principle: an AI model may interpret market evidence, but it must never be able to grant itself permission to risk capital."
    },
    @{
        Image = Join-Path $assetsDir "01-overview.png"
        Narration = "The Overview is the desk's control room. This is a fresh one-hundred-thousand dollar Alpaca paper account with Options Level Three. The scheduler checks in every fifteen minutes, the private Qwen committee is online, and public order authority is visibly locked. Performance comes from Alpaca portfolio history and is aligned with S P Y on the same dates. Below that, twenty tradable symbols are ranked before each cycle, while only the strongest candidates pay the cost of full option-chain and committee analysis."
    },
    @{
        Image = Join-Path $assetsDir "02-market-pulse.png"
        Narration = "Market Pulse provides the context around every decision. Forty instruments cover the S and P 500, Nasdaq, small caps, commodities, rates, volatility, sectors, and liquid market leaders. Each card has its own price scale and recent path, so the agent can distinguish a company-specific move from a wider risk-on, inflation, rates, or volatility regime."
    },
    @{
        Image = Join-Path $assetsDir "03-strategy-lab.png"
        Narration = "Strategy Lab tests what happens before capital moves. Pick any symbol and compare current evidence with a volatility shock, bullish tape, or bearish tape. A reproducible Monte Carlo engine simulates up to twenty-thousand paths and settles the exact proposed option legs. It reports probability of profit, value at risk, expected shortfall, break-even regions, and near-maximum-loss probability. Crucially, simulation can veto a structure, but it can never turn a failing proposal into an approved trade."
    },
    @{
        Image = Join-Path $assetsDir "04-intelligence.png"
        Narration = "The Intelligence view joins company news and sentiment with S E C filing events and F R E D macro signals. Source health and timestamps stay visible. These inputs help the committee explain market context, while contract selection and risk still remain deterministic."
    },
    @{
        Image = Join-Path $assetsDir "05-decision-journal.png"
        Narration = "The Decision Journal is the system's memory. Every autonomous review creates a hash-chained passport, including decisions that preserve cash. It shows review coverage and the most frequent vetoes without pretending that previews are returns. Strategy promotion stays locked until at least five broker-verified closed outcomes exist."
    },
    @{
        Image = Join-Path $assetsDir "06-decision-passport.png"
        Narration = "Open a passport and the entire argument is inspectable: the original thesis, maximum loss, every gate, and three independent model opinions with their exact Private Qwen provenance. This proposal was rejected, so no order was sent. If a proposal passes, Alpaca records submission, fill, cancellation, and managed exit events in a separate append-only lifecycle stream."
    },
    @{
        Image = Join-Path $docsDir "Volition_Hackathon_Cover.png"
        Narration = "Volition combines private adversarial reasoning, deterministic risk, Alpaca options infrastructure, Monte Carlo stress testing, and honest broker-backed memory in one deployed product. It is not an A I that always trades. It is an agent judges can trust when it says no."
    }
)

Add-Type -AssemblyName System.Speech
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
$preferredVoice = $speech.GetInstalledVoices() |
    ForEach-Object { $_.VoiceInfo.Name } |
    Where-Object { $_ -eq "Microsoft Mark" } |
    Select-Object -First 1
if ($preferredVoice) {
    try {
        $speech.SelectVoice($preferredVoice)
    }
    catch {
        # Some sandboxed Windows sessions enumerate voices that cannot be
        # selected explicitly. The synthesizer's default voice remains usable.
    }
}
$speech.Rate = 0
$speech.Volume = 100

$segments = @()
for ($index = 0; $index -lt $scenes.Count; $index++) {
    $sceneNumber = "{0:D2}" -f ($index + 1)
    $scene = $scenes[$index]
    if (-not (Test-Path -LiteralPath $scene.Image)) {
        throw "Missing scene image: $($scene.Image)"
    }

    $audioFile = Join-Path $buildDir "$sceneNumber.wav"
    $segmentFile = Join-Path $buildDir "$sceneNumber.mp4"
    $speech.SetOutputToWaveFile($audioFile)
    $speech.Speak($scene.Narration)
    $speech.SetOutputToDefaultAudioDevice()

    $stream = [System.IO.File]::OpenRead($audioFile)
    $reader = New-Object System.IO.BinaryReader($stream)
    try {
        $reader.BaseStream.Position = 12
        $byteRate = 0
        $dataSize = 0
        while ($reader.BaseStream.Position + 8 -le $reader.BaseStream.Length) {
            $chunkId = [System.Text.Encoding]::ASCII.GetString($reader.ReadBytes(4))
            $chunkSize = [int64]$reader.ReadUInt32()
            $chunkStart = $reader.BaseStream.Position
            if ($chunkId -eq "fmt ") {
                $reader.BaseStream.Position = $chunkStart + 8
                $byteRate = $reader.ReadUInt32()
            }
            elseif ($chunkId -eq "data") {
                $dataSize = $chunkSize
                break
            }
            $reader.BaseStream.Position = $chunkStart + $chunkSize + ($chunkSize % 2)
        }
        if ($byteRate -le 0 -or $dataSize -le 0) {
            throw "Unable to read WAV duration for $audioFile"
        }
        $duration = ($dataSize / $byteRate) + 0.35
    }
    finally {
        $reader.Dispose()
        $stream.Dispose()
    }

    $durationText = $duration.ToString("0.000", $culture)
    $videoFilter = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#f4f0e5,format=yuv420p"

    & $FfmpegExe -hide_banner -loglevel error -y `
        -loop 1 -framerate 1 -i $scene.Image `
        -i $audioFile `
        -vf $videoFilter `
        -af "apad=pad_dur=0.35" `
        -t $durationText `
        -r 1 `
        -c:v libx264 -preset superfast -tune stillimage -crf 28 `
        -c:a aac -b:a 128k `
        $segmentFile

    if ($LASTEXITCODE -ne 0) {
        throw "FFmpeg failed while rendering scene $sceneNumber"
    }
    $segments += $segmentFile
}

$concatFile = Join-Path $buildDir "concat.txt"
$concatLines = $segments | ForEach-Object {
    "file '$($_.Replace('\', '/'))'"
}
[System.IO.File]::WriteAllLines($concatFile, $concatLines)

& $FfmpegExe -hide_banner -loglevel error -y `
    -f concat -safe 0 -i $concatFile `
    -c copy -movflags +faststart `
    $outputFile

if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg failed while joining the walkthrough"
}

$speech.Dispose()
Write-Output $outputFile
