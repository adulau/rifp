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
- mandatory fixed 56-byte OBJECT_DESCRIPTOR plus DATA, END and CANCEL frames;
- optional extended JSON MANIFEST frames with namespaced extension data;
- CRC-32 on every frame;
- CRC-32 and SHA-256 verification of the complete encoded image;
- out-of-order delivery, repetition and duplicate detection;
- explicit media types and content-encoding names; and
- named radio profiles that are independent of the framing protocol.

Unknown advisory extensions are ignored. Unknown critical extensions cause the
individual frame to be rejected, allowing future features such as encryption,
signatures, FEC and repair requests to be introduced safely.

## Potential Use Cases

RIFP is intended for the local distribution of visual information to nearby electronic displays, particularly in environments where Internet connectivity is unavailable, unreliable, undesirable, or unnecessarily complex.

Potential use cases include:

* **Local information and alert displays:** Distributing menus, schedules, operational notices, safety instructions, and alerts to nearby electronic displays. One example is displaying a restaurant menu on a ferry where Internet connectivity is unavailable or unreliable.

* **Visitor information:** Providing practical information to visitors when they arrive at a location, such as opening hours, maps, instructions, local rules, event schedules, or contact details.

* **Low-impact public information and advertising:** Displaying temporary information, announcements, or advertisements without permanently altering or visually cluttering the public space with printed posters, stickers, or signs.

* **Museums, galleries, and art exhibitions:** Associating descriptions, contextual information, translations, or multimedia references with artworks without attaching visually intrusive paper labels next to each work.

* **Electronic price labels:** Updating product prices, promotions, stock information, origin details, or other commercial information on electronic shelf labels.

* **Emergency and disaster communications:** Broadcasting maps, evacuation instructions, shelter information, medical guidance, or status updates to battery-powered displays when mobile and Internet infrastructure is unavailable.

* **Public transport information:** Updating departure times, route changes, platform information, disruptions, and passenger instructions at stops, stations, ferries, or temporary boarding points.

* **Temporary events and exhibitions:** Distributing programmes, room assignments, speaker information, schedules, and wayfinding information during conferences, festivals, fairs, and community events.

* **Tourism and cultural heritage sites:** Providing historical information, walking-route guidance, translations, accessibility information, and temporary notices at monuments, archaeological sites, nature trails, and remote attractions.

* **Hotels, campsites, and accommodation facilities:** Updating breakfast times, activity schedules, weather warnings, check-out instructions, transport information, and local recommendations.

* **Schools, universities, and campuses:** Distributing room changes, timetables, examination notices, emergency instructions, and event information to local displays.

* **Industrial and operational environments:** Displaying equipment status, maintenance instructions, safety notices, work orders, and production information in warehouses, workshops, ports, and construction sites.

* **Healthcare and care facilities:** Providing room information, queue status, visiting instructions, hygiene notices, or non-sensitive guidance without requiring each display to maintain an Internet connection.

* **Remote and off-grid locations:** Updating information boards in mountain shelters, islands, rural areas, nature reserves, temporary camps, or other locations with limited infrastructure.

* **Mobile or rapidly deployed installations:** Supplying information to displays installed in temporary shelters, field hospitals, emergency coordination centres, pop-up shops, mobile exhibitions, or humanitarian operations.

* **Community noticeboards:** Distributing local announcements, municipal information, event notices, and public-service messages to low-power electronic noticeboards.

* **Personal and domestic displays:** Updating household dashboards, shared calendars, reminders, weather information, or home-automation status on low-power displays without giving every device direct Internet access.

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
  --extended-manifest \
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
TLV extension model, compact descriptor and optional manifest schemas,
fragmentation rules, CRC algorithm, CPFSK profile, security considerations,
test vector and proposed IANA registries.

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
