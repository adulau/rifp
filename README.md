# RIFP radio image sender and receiver

The **Radio Image Framing Protocol (RIFP) 1.0** is an experimental, extensible standard 
for sending images over low-rate radio links.

The default `rifp-cpfsk-4800` profile uses binary continuous-phase FSK and can
be deployed around 433.92 MHz where local regulation permits it. RIFP itself is
not tied to 433 MHz or to FSK.

The implementation is not an analogue WEFAX receiver. The `group3` and
`group4` codecs nevertheless use real CCITT Group 3/4 TIFF facsimile
compression.

![A sample test file sent over RF](https://raw.githubusercontent.com/adulau/rifp/refs/heads/main/example/previews/full-rle.png)

## Protocol properties

RIFP 1.0 provides:

- a 28-byte versioned base header;
- 64-bit random session identifiers;
- 32-bit sequence and chunk-count fields;
- advisory and critical flags;
- extensible 16-bit type/length/value header fields;
- MANIFEST, DATA, END and CANCEL frame types;
- JSON manifests with namespaced extension data;
- CRC-32 on every frame;
- CRC-32 and SHA-256 verification of the complete encoded image;
- out-of-order delivery, repetition and duplicate detection;
- explicit media types and content-encoding names; and
- named radio profiles that are independent of the framing protocol.

Unknown advisory extensions are ignored. Unknown critical extensions cause the
individual frame to be rejected, allowing future features such as encryption,
signatures, FEC and repair requests to be introduced safely.

## Dependencies

```bash
python3 -m pip install -r requirements_radiofax.txt
```

For live RF, install SoapySDR, its Python bindings and the hardware module for
your SDR. Transmission requires TX-capable hardware such as a HackRF, LimeSDR,
PlutoSDR or USRP. Many RTL-SDR devices are receive-only but can run the
receiver.

## Offline conformance loopback

Run the commands from the directory containing all three Python files:

```bash
python3 radiofax_sender.py example.png \
  --preset small --codec group4 --bits 1 \
  --packet-repeats 1 --manifest-repeats 1 \
  --sender-id CIRCL \
  --content-hint "scheduled weather image" \
  --manifest-extension 'org.example.source={"camera":"north"}' \
  --duty-cycle 1 --iq-output example.cf32

python3 radiofax_receiver.py \
  --iq-input example.cf32 \
  --output-dir received-radiofax
```

Both sides must use the same sample and symbol rates. Defaults are 96
ksample/s and 4,800 symbols/s. IQ-file mode uses `--iq-gap` and does not add
long silence representing regulatory duty-cycle pacing.

Manifest extensions use `NAMESPACE=JSON`. The namespace must contain a dot or
colon, for example `org.example.feature={"enabled":true}`.

## Live recurring transmission

```bash
python3 radiofax_sender.py images/ \
  --recursive \
  --device driver=hackrf \
  --frequency 433.92M \
  --preset small \
  --codec auto \
  --bits 1 \
  --cycles 0 \
  --interval 900 \
  --packet-repeats 2 \
  --manifest-repeats 3 \
  --manifest-every 8 \
  --duty-cycle 0.10
```

`--cycles 0` repeats the set indefinitely. Directories are rescanned at the
start of each cycle, so newly added images are included. `--shuffle` changes
the order for every cycle.

## Fixed-frequency reception

```bash
python3 radiofax_receiver.py \
  --device driver=rtlsdr \
  --frequency 433.92M \
  --gain 25 \
  --output-dir received-radiofax
```

## Wideband discovery

```bash
python3 radiofax_receiver.py \
  --device driver=rtlsdr \
  --discover \
  --scan-center 434.0M \
  --scan-span 1.9M \
  --scan-sample-rate 2.4M \
  --gain 25
```

Discovery performs energy detection and then retunes to the strongest
candidate. Identification still requires a valid RIFP synchronization word,
header and CRC.

## Encoding profiles

Line art or text, lossless and normally smallest:

```bash
--preset tiny --bits 1 --codec group4 --packet-repeats 1
```

Photographs, lossy and much smaller:

```bash
--preset small --bits 8 --codec jpeg --jpeg-quality 35 --packet-repeats 1
```

Grayscale diagrams, lossless:

```bash
--preset small --bits 4 --codec zlib --packet-repeats 1
```

`--codec auto` selects the smallest successful lossless encoding. Add
`--allow-lossy-auto` to include JPEG in the comparison.

## Tests

```bash
python3 -m unittest -v test_rifp_protocol.py
```

The implementation has also been exercised with offline IQ loopbacks for
Group 3, Group 4, PNG, JPEG, raw raster, RLE, ZLIB and automatic codec
selection. A repeated-frame transfer was reconstructed after adding synthetic
noise and a +2.5 kHz carrier offset.

## Internet-Draft

The [Radio Image Framing Protocol (RIFP) draft-dulaunoy-rifp-00](https://datatracker.ietf.org/doc/draft-dulaunoy-rifp/) defines the base header, 
TLV extension model, manifest schema, fragmentation rules, CRC algorithm, CPFSK profile, security considerations, test vector and proposed IANA registries.

## Radio and regulatory notes

Use a shielded setup, attenuators, a dummy load or very low output power during
development. Confirm permitted frequency, effective radiated power, occupied
bandwidth, duty cycle, equipment requirements and licensing conditions before
connecting an antenna. `--duty-cycle` only paces software bursts; it does not
certify compliance or measure radiated emissions.

## Open-Source License 

The software is open-source under a BSD 2-Clause License. RIFP is free of patent or specific
restriction on the standard.

~~~
BSD 2-Clause License

Copyright (c) 2026, Alexandre Dulaunoy

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
~~~
