%%%
title = "Radio Image Framing Protocol (RIFP)"
abbrev = "RIFP"
ipr = "trust200902"
area = "Internet"
workgroup = "Individual Submission"
keyword = ["radio", "image", "facsimile", "FSK", "fragmentation"]

[seriesInfo]
status = "experimental"
name = "Internet-Draft"
value = "draft-dulaunoy-rifp-00"
stream = "IETF"

date = 2026-07-25T00:00:00Z

[[author]]
initials = "A."
surname = "Dulaunoy"
fullname = "Alexandre Dulaunoy"
organization = "CIRCL"
%%%

.# Abstract

This document specifies the Radio Image Framing Protocol (RIFP), a compact,
unidirectional object-transfer protocol intended for transmitting images over
low-rate radio links.  RIFP defines independently synchronized frames, a
versioned and extensible binary header, fragmentation and reassembly rules, a
JSON manifest, per-frame CRC-32 protection, whole-object SHA-256 verification,
and registries for future frame types, flags, header extensions, media
encodings, and radio profiles.

RIFP is independent of a particular frequency allocation or modulation.  This
document also defines an initial continuous-phase binary frequency-shift keying
profile named `rifp-cpfsk-4800`.  The profile can be used on frequencies where
local regulation permits such operation; 433.92 MHz is a common deployment
example but is not mandated by this specification.

{mainmatter}

# Introduction

Low-rate radio systems are often used to move small telemetry messages, but
there are also practical cases for periodically sending weather maps, camera
snapshots, diagrams, forms, or facsimile-like monochrome images.  Existing
analogue facsimile systems generally bind image representation, timing, and
modulation into one format.  That makes experimentation with compression,
packet repetition, stronger integrity checks, or new physical layers harder.

RIFP separates three concerns:

* an image object and its metadata;
* an extensible fragmentation and framing layer; and
* a named radio profile describing modulation and synchronization.

The initial protocol is intentionally unidirectional.  A sender can repeat
manifest and data frames to improve reception on lossy channels.  Future
specifications can add repair requests, forward-error correction,
authentication, encryption, or bidirectional operation without redefining the
base frame.

# Conventions and Terminology

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**,
**SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **NOT RECOMMENDED**, **MAY**, and
**OPTIONAL** in this document are to be interpreted as described in BCP 14
[@!RFC2119] [@!RFC8174] when, and only when, they appear in all capitals, as
shown here.

All multi-octet integers are unsigned and encoded in network byte order.
Bits within an octet are transmitted most significant bit first.

Object:
: The complete encoded image byte sequence being transferred.

Session:
: One transfer of one object, identified by a 64-bit Session ID.

Frame:
: A complete RIFP protocol data unit consisting of a base header, optional
  header extensions, payload, and CRC-32 trailer.

Radio profile:
: A named set of physical-layer and synchronization parameters used to carry
  RIFP frames.

Critical extension:
: A flag or TLV that changes processing in a way that cannot safely be ignored.

# Protocol Overview

A sender performs the following operations:

1. It scales and encodes an image.
2. It assigns a 64-bit Session ID.
3. It constructs a JSON manifest describing the object.
4. It divides the encoded object into numbered chunks.
5. It sends one or more MANIFEST frames, DATA frames for each chunk, and an
   END frame.
6. It MAY repeat any MANIFEST or DATA frame.

A receiver searches for the radio-profile synchronization word, parses the
RIFP header, rejects unsupported critical features, validates the frame CRC,
and stores valid chunks by Session ID and Sequence Number.  Once all chunks
are present, the receiver concatenates them in sequence order and validates
the complete size, CRC-32, and SHA-256 digest before decoding the image.

Frames are self-synchronizing and independently protected.  A receiver does
not need to receive a burst from its beginning, and it can process frames in
any order.

# RIFP Frame Format

A RIFP frame begins immediately after any radio-profile preamble and
synchronization word.  The minimum header is 28 octets.

~~~
  0                   1                   2                   3
  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 | Major Version | Minor Version |  Frame Type   | Header Length |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                            Flags                              |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                                                               |
 +                         Session ID                            +
 |                                                               |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                        Sequence Number                        |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                          Total Count                          |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |        Payload Length         |           Reserved            |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                 Header Extensions, if any ...                 |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                         Payload ...                           |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                            CRC-32                             |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
~~~

Major Version:
: An 8-bit protocol major version.  This specification defines value 1.

Minor Version:
: An 8-bit protocol minor version.  This specification defines value 0.

Frame Type:
: An 8-bit value identifying the payload semantics.

Header Length:
: Total header length in octets, including the 28-octet base header and all
  TLVs.  It **MUST** be between 28 and 255 inclusive.

Flags:
: A 32-bit field.  Bits 0 through 15 are advisory.  Bits 16 through 31 are
  critical.

Session ID:
: A 64-bit identifier shared by all frames in one transfer.  Senders **SHOULD**
  generate this value using a cryptographically secure random-number generator.

Sequence Number:
: Frame-type-specific sequence information.

Total Count:
: The number of DATA chunks in the session.

Payload Length:
: Payload size in octets.  The maximum is 65535.

Reserved:
: This field **MUST** be transmitted as zero.  A receiver **MUST** discard a
  frame in which it is non-zero.

Header Extensions:
: Zero or more TLVs, as specified in (#header-extensions).

CRC-32:
: CRC-32/ISO-HDLC over the complete header, including extensions, followed by
  the payload.  The radio preamble and synchronization word are not included.

## Version Processing

A receiver that does not support the received Major Version **MUST** discard
the frame.  A receiver **MAY** process a higher Minor Version when it
understands the Frame Type and all critical flags and critical TLVs.  Unknown
non-critical features **MUST NOT** by themselves cause rejection.

A future specification that changes base-field interpretation or processing in
an incompatible manner **MUST** allocate a new Major Version.

## Flags

The following flag is defined:

| Bit | Name | Semantics |
|-----|------|-----------|
| 0 | RETRANSMISSION | The frame repeats information sent previously. |

Bits 1 through 15 are unassigned advisory flags.  Receivers **MUST** ignore
unknown advisory flags.

Bits 16 through 31 are critical flags.  A receiver **MUST** discard a frame
containing any critical flag it does not understand.

# Header Extensions {#header-extensions}

Header extensions use the following TLV format:

~~~
  0                   1                   2                   3
  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |            TLV Type           |          TLV Length           |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                         TLV Value ...                         |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
~~~

TLV Type:
: A 16-bit type.  Bit 15 is the Critical bit.  Bits 0 through 14 identify the
  extension type.

TLV Length:
: Length of the value in octets, excluding the four-octet TLV header.

TLV Value:
: Type-specific bytes.  TLVs are concatenated without alignment padding.

An unknown TLV with the Critical bit set **MUST** cause the frame to be
discarded.  An unknown TLV without the Critical bit set **MUST** be ignored.
A sender **MUST NOT** include two critical TLVs with the same base type unless
the specification for that TLV explicitly permits repetition.

This document defines these non-critical TLVs:

| Base Type | Name | Value |
|-----------|------|-------|
| 1 | Sender ID | A valid UTF-8 sender identifier. |
| 2 | Radio Profile | A valid UTF-8 radio-profile name. |
| 3 | Content Hint | A valid UTF-8 short description for operator display. |

Text TLV values **MUST** be valid UTF-8 and **MUST NOT** contain a NUL octet.
Receivers **SHOULD** impose a local display-length limit.

# Frame Types

## MANIFEST Frame

Frame Type 1 is MANIFEST.  Its payload is a UTF-8 JSON object conforming to
[@!RFC8259].  Sequence Number **MUST** be zero.  Total Count **MUST** equal the
number of DATA chunks.

A sender **SHOULD** transmit the MANIFEST more than once and **SHOULD** repeat
it periodically during a long session.  A receiver **MUST** treat identical
MANIFEST frames as duplicates.  If two valid manifests for the same session
conflict, the receiver **MUST NOT** combine their DATA chunks unless a future
extension defines conflict resolution.

## DATA Frame

Frame Type 2 is DATA.  The payload contains one contiguous chunk of the
encoded object.  Sequence Number is zero-based.  Total Count is the number of
chunks in the session and **MUST** match the manifest.

All DATA frames except the final one **SHOULD** have the manifest's
`chunk_size`.  The final DATA frame **MAY** be shorter.  A receiver **MUST**
ignore a DATA frame whose Sequence Number is greater than or equal to Total
Count.

A repeated DATA frame **MUST** contain exactly the same payload as the earlier
frame with the same Session ID and Sequence Number.  A receiver that observes
conflicting valid payloads for one sequence **MUST** abandon or quarantine the
session.

## END Frame

Frame Type 3 is END.  Sequence Number and Total Count **MUST** both equal the
number of DATA chunks.  Its payload is exactly 44 octets:

~~~
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                                                               |
 +                    Encoded Size (64 bits)                     +
 |                                                               |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                    Payload CRC-32 (32 bits)                    |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                                                               |
 +                                                               +
 |                    Payload SHA-256 (256 bits)                  |
 +                                                               +
 |                                                               |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
~~~

The END frame is a completion hint and repeats integrity values from the
manifest.  Receipt of END is not required for completion when the manifest and
all DATA chunks have already been received and verified.

## CANCEL Frame

Frame Type 4 is CANCEL.  It indicates that the sender has abandoned the
session.  Sequence Number and Total Count **SHOULD** be zero.  The payload
**MAY** be empty or contain a short UTF-8 reason.  A receiver **SHOULD** discard
incomplete state for the indicated Session ID.

## Unknown Frame Types

A receiver **MUST** validate the complete header and CRC before deciding that a
Frame Type is unknown.  It **MUST** then ignore the frame.  Unknown frame types
do not invalidate other frames in the same session.

# Manifest Format

The manifest is a JSON object encoded as UTF-8 without a byte-order mark.
Field names and string values are case sensitive.  For interoperable JSON
processing, integer values **MUST** be in the range -(2^53) + 1 through
(2^53) - 1.  Unknown fields **MUST** be ignored unless a future specification
explicitly marks them as critical through a critical header extension or
critical flag.

The following fields are required:

| Field | Type | Description |
|-------|------|-------------|
| `protocol` | string | Exactly `rifp`. |
| `protocol_version` | string | Dotted major and minor version, initially `1.0`. |
| `session_id` | string | Session ID as exactly 16 lowercase hexadecimal digits. |
| `filename` | string | Suggested source filename. |
| `media_type` | string | Media type of the encoded object. |
| `content_encoding` | string | Registered RIFP encoding name. |
| `width` | integer | Decoded image width in pixels. |
| `height` | integer | Decoded image height in pixels. |
| `bits_per_pixel` | integer | Nominal decoded grayscale depth. |
| `encoded_size` | integer | Complete encoded object size in octets. |
| `payload_crc32` | integer | Unsigned CRC-32 of the complete object. |
| `payload_sha256` | string | Lowercase hexadecimal SHA-256 digest. |
| `chunk_size` | integer | Nominal DATA payload size in octets. |
| `chunk_count` | integer | Number of DATA chunks. |

The following fields are optional:

| Field | Type | Description |
|-------|------|-------------|
| `raw_size` | integer | Size of the uncompressed packed raster. |
| `created` | string | UTC timestamp in Internet date-time format [@!RFC3339]. |
| `radio_profile` | string | Radio-profile name used by the sender. |
| `extensions` | object | Namespaced manifest extensions. |

The receiver **MUST** verify that `session_id` matches the binary header and
that `chunk_count` matches Total Count.  It **MUST** reject dimensions, sizes,
or chunk counts that exceed local resource limits.

An example manifest follows:

``` json
{
  "bits_per_pixel": 1,
  "chunk_count": 2,
  "chunk_size": 192,
  "content_encoding": "ccitt-group4",
  "created": "2026-07-25T08:00:00Z",
  "encoded_size": 246,
  "extensions": {
    "org.example.camera": {
      "position": "north"
    }
  },
  "filename": "weather-map.png",
  "height": 96,
  "media_type": "image/tiff",
  "payload_crc32": 188765321,
  "payload_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "protocol": "rifp",
  "protocol_version": "1.0",
  "radio_profile": "rifp-cpfsk-4800",
  "raw_size": 1536,
  "session_id": "0123456789abcdef",
  "width": 128
}
```

Extension keys in `extensions` **SHOULD** use a reverse-domain or URI-based
namespace controlled by the extension author.  An extension that is required
to decode or authenticate the object **MUST** also be signaled by a critical
header TLV or critical flag so that an older receiver cannot silently ignore
it.

## Initial Media and Encoding Combinations

| Media Type | Content Encoding | Meaning |
|------------|------------------|---------|
| `image/tiff` | `ccitt-group3` | TIFF containing CCITT Group 3 facsimile data. |
| `image/tiff` | `ccitt-group4` | TIFF containing CCITT Group 4 facsimile data. |
| `image/png` | `identity` | Complete PNG file. |
| `image/jpeg` | `identity` | Complete JPEG file. |
| `image/rifp-raster` | `raw` | Packed grayscale raster. |
| `image/rifp-raster` | `rle8` | Byte-oriented run-length encoding of the packed raster. |
| `image/rifp-raster` | `zlib` | ZLIB-compressed packed raster [@!RFC1950]. |

For the vendor raster media type, pixels are in row-major order from top left
to bottom right.  Supported grayscale depths are 1, 2, 4, and 8 bits per pixel.
Samples are packed most significant sample first.  Rows are not separately
padded; only the complete pixel sequence is padded with zero-valued samples to
an octet boundary.

The `rle8` encoding represents each run as two octets: an unsigned run length
from 1 through 255 followed by the repeated octet value.  A zero run length is
invalid.

# Fragmentation, Repetition, and Reassembly

The sender chooses a fixed nominal chunk size for a session.  Small chunks
reduce the cost of losing one frame but increase header overhead.  The initial
CPFSK profile recommends 192 payload octets per DATA frame.

A receiver maintains reassembly state keyed by Session ID.  It **MUST** accept
valid DATA chunks before the MANIFEST arrives, subject to local resource
limits.  It **MUST** de-duplicate frames by at least Session ID, Frame Type,
Sequence Number, and content.

A receiver completes a session only when:

* a valid MANIFEST is available;
* every DATA Sequence Number from zero through `chunk_count - 1` is present;
* concatenation yields exactly `encoded_size` octets;
* CRC-32 matches `payload_crc32`; and
* SHA-256 matches `payload_sha256`.

The CRC protects against accidental corruption and assists efficient rejection.
SHA-256 provides stronger object-integrity checking but does not authenticate
the sender [@!RFC6234].

A receiver **SHOULD** expire incomplete sessions, limit the number of concurrent
sessions, limit total buffered bytes, and limit decoded image dimensions.

# CRC-32

RIFP uses CRC-32/ISO-HDLC, also commonly called the Ethernet or ZIP CRC-32.  It
uses the polynomial 0x04C11DB7 in reflected form, initial value 0xFFFFFFFF,
reflected input and output, and final XOR 0xFFFFFFFF.

The check value for the ASCII octets `123456789` is 0xCBF43926.

The CRC field itself is transmitted as a 32-bit network-order integer and is
not included in the calculation.

# Initial Radio Profile: rifp-cpfsk-4800

RIFP does not assign a carrier frequency.  Deployments **MUST** comply with the
frequency, power, bandwidth, duty-cycle, equipment, and licensing rules that
apply in their jurisdiction.

The `rifp-cpfsk-4800` profile has these parameters:

| Parameter | Value |
|-----------|-------|
| Modulation | Continuous-phase binary FSK |
| Symbol rate | 4800 symbols per second |
| Mark deviation | +4000 Hz |
| Space deviation | -4000 Hz |
| Symbol mapping | bit 1 is mark; bit 0 is space |
| Bit order | Most significant bit first |
| Preamble | 48 octets of 0x55 |
| Sync word | 0xD391C5A7 |
| Recommended occupied channel bandwidth | 25 kHz |
| Recommended IQ sample rate | 96000 samples per second |

Continuous phase is maintained throughout one transmitted frame.  Phase need
not be continuous between frames.  The preamble and sync word precede every
frame and are not part of the RIFP CRC calculation.

A sender **MAY** use a different sample rate when it is an integer multiple of
the symbol rate.  Sample rate is an implementation property and is not sent
over the air.

A receiver can discover a transmission by energy detection followed by search
for the synchronization word and validation of a complete RIFP frame.  Energy
detection alone is not sufficient to identify a RIFP signal.

# Extensibility Model

RIFP provides four extension mechanisms:

1. Minor versions for backward-compatible clarifications and additions.
2. Advisory and critical header flags.
3. Advisory and critical header TLVs.
4. Namespaced manifest members and registered media encodings.

A specification defining a new feature **MUST** state whether older receivers
can safely ignore it.  Features affecting decryption, decompression,
authentication, chunk interpretation, or object integrity normally require a
critical flag or TLV.

Potential future extensions include:

* forward-error-correction blocks;
* selective repair requests and acknowledgements;
* digital signatures and sender certificates;
* authenticated encryption;
* fountain or rateless coding;
* multiple objects in one session;
* colour and multispectral raster profiles; and
* radio profiles using LoRa, GMSK, OFDM, audio FSK, or wired transports.

# Security Considerations

RIFP's CRC and SHA-256 fields provide error detection and object consistency.
They do not identify the sender and do not prevent deliberate modification by
an attacker capable of replacing all frames and integrity values.

Unlicensed and shared radio channels are susceptible to eavesdropping,
spoofing, replay, traffic analysis, interference, and intentional jamming.
Applications requiring confidentiality or source authentication **MUST** use a
future authenticated-encryption or signature extension, or provide equivalent
protection outside RIFP.

Session IDs are not secrets.  Random generation reduces accidental collision
and makes casual injection into an active session less convenient, but it is
not an authentication mechanism.

Receivers process attacker-controlled metadata and compressed image data.
Implementations **MUST**:

* bound manifest size, object size, dimensions, chunk count, concurrent
  sessions, and decompression output;
* reject malformed UTF-8 and malformed JSON;
* protect image decoders against decompression bombs and memory exhaustion;
* sanitize filenames and never use a received filename as an unrestricted
  filesystem path;
* avoid executing or interpreting received metadata as code; and
* reject unknown critical flags and TLVs.

A receiver should assume that duplicate, reordered, conflicting, and truncated
frames are normal hostile inputs rather than exceptional conditions.

# Privacy Considerations

Image content, filenames, sender identifiers, timestamps, and transmission
patterns are visible to passive observers in the base protocol.  A stable
Sender ID can enable long-term tracking.  Deployments **SHOULD** omit optional
identifiers that are not operationally necessary.

# IANA Considerations

IANA is requested to create a top-level registry named "Radio Image Framing
Protocol (RIFP) Parameters" containing the following subregistries.
Registration procedures use the terminology of [@!RFC8126].

## RIFP Frame Types

The registry contains 8-bit values.  Values 0x00 and 0xFF are Reserved.
Values 0x01 through 0x3F require Specification Required.  Values 0x40 through
0x7F are for Experimental Use.  Values 0x80 through 0xFE are for Private Use.

Initial registrations are MANIFEST (0x01), DATA (0x02), END (0x03), and CANCEL
(0x04), all referencing this document.

## RIFP Header TLV Base Types

The registry contains 15-bit values because the high bit is the Critical bit.
Value 0 is Reserved.  Values 1 through 16383 require Specification Required.
Values 16384 through 32766 are for Private Use.  Value 32767 is Reserved.

Initial registrations are Sender ID (1), Radio Profile (2), and Content Hint
(3), all referencing this document.

## RIFP Flags

The registry records flag bit numbers, names, whether the bit is advisory or
critical, and references.  Bits 0 through 15 are advisory and bits 16 through
31 are critical.  Registration requires Specification Required.

The initial registration is RETRANSMISSION at bit 0.

## RIFP Content Encodings

Encoding names are case-sensitive ASCII strings.  Registration requires
Specification Required.  Each registration records compatible media types and
a reference.

Initial registrations are `identity`, `ccitt-group3`, `ccitt-group4`, `raw`,
`rle8`, and `zlib`.

## image/rifp-raster Media Type

IANA is requested to register the following media type according to
[@!RFC6838].

Type name:
: image

Subtype name:
: rifp-raster

Required parameters:
: None.

Optional parameters:
: None.

Encoding considerations:
: Binary.

Security considerations:
: See the Security Considerations section of this document.  The dimensions
  and sample depth can cause large allocations, and encoded forms can expand
  substantially during decompression.

Interoperability considerations:
: Pixel packing and grayscale depths are defined in this document.  The RIFP
  `content_encoding` value identifies raw, RLE8, or ZLIB representation.

Published specification:
: This document.

Applications that use this media type:
: RIFP senders, receivers, gateways, and diagnostic tools.

Fragment identifier considerations:
: None.

Additional information:
: No deprecated alias, file extension, or Macintosh file type is defined.

Person and email address to contact for further information:
: Alexandre Dulaunoy, CIRCL.

Intended usage:
: COMMON.

Restrictions on usage:
: None.

Author:
: Alexandre Dulaunoy.

Change controller:
: IETF.

## RIFP Radio Profiles

Profile names are case-sensitive ASCII strings.  Names beginning with `x-` are
for Private Use.  Other registrations require Specification Required.

The initial registration is `rifp-cpfsk-4800`.

# Implementation Status

This section is included while the document is an Internet-Draft and can be
removed before publication.

A Python reference implementation supports:

* MANIFEST, DATA, END, and CANCEL parsing;
* extensible headers and unknown-critical-extension rejection;
* CCITT Group 3 and Group 4 TIFF, PNG, JPEG, raw, RLE, and ZLIB payloads;
* CPFSK generation and reception through SoapySDR;
* raw complex64 IQ loopback files;
* wideband energy discovery followed by sync and CRC validation;
* image scaling, grayscale quantization, and periodic directory transmission;
* complete-object CRC-32 and SHA-256 verification; and
* receiver resource limits and filename sanitization.

# Test Vector

This test vector omits the radio-profile preamble and synchronization word.
It is a DATA frame with Session ID 0x0123456789ABCDEF, Sequence Number 1,
Total Count 2, no flags, no extensions, and the three-octet payload `abc`.

~~~
Base header:
0100021c000000000123456789abcdef000000010000000200030000

Payload:
616263

CRC-32:
ed2bb111

Complete frame after sync word:
0100021c000000000123456789abcdef000000010000000200030000
616263ed2bb111
~~~

{backmatter}

# Acknowledgements

The author thanks the amateur-radio, software-defined-radio, facsimile, and
open-source communities whose experiments motivated this protocol design.
