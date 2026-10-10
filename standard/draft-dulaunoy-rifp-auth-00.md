%%%
title = "Pre-Shared-Key Object Authentication for RIFP"
abbrev = "RIFP Object Authentication"
ipr = "trust200902"
area = "Internet"
keyword = ["radio", "image", "authentication", "HMAC", "RIFP"]
docName = "draft-dulaunoy-rifp-auth-00"
submissiontype = "independent"
category = "info"
date = 2026-10-10T00:00:00Z

[seriesInfo]
status = "experimental"
name = "Internet-Draft"
value = "draft-dulaunoy-rifp-auth-00"
stream = "independent"

[[author]]
initials = "A."
surname = "Dulaunoy"
fullname = "Alexandre Dulaunoy"
organization = "Computer Incident Response Center Luxembourg"
 [author.address]
 email = "alexandre.dulaunoy@circl.lu"
%%%

.# Abstract

This document defines an optional object authentication extension for the
Radio Image Framing Protocol (RIFP). A critical header TLV authenticates the
compact object descriptor using HMAC-SHA-256 and a pre-shared key. The
descriptor binds the encoded image digest and decoding parameters to a
session and chunk count. The extension uses existing RIFP framing,
fragmentation, retransmission and offline IQ workflows. It does not provide
confidentiality, replay protection or a public-key digital signature.

{mainmatter}

# Introduction

The CRC-32 and SHA-256 fields in [@!RIFP] detect accidental corruption, but
an attacker can replace an image and recompute both fields. This extension
authenticates the mandatory 56-octet OBJECT_DESCRIPTOR with a key shared by
the sender and receiver. After reassembly, the receiver checks the image
against that authenticated descriptor before decoding or publishing it.

Implementation and use of this extension are OPTIONAL. Ordinary unsigned
RIFP transfers remain valid. Applications needing authenticated images MUST
configure the receiver to require authentication; detecting a TLV alone
cannot prevent an attacker from stripping it.

# Conventions and Terminology

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**,
**SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **NOT RECOMMENDED**, **MAY**, and
**OPTIONAL** in this document are to be interpreted as described in
[@!RFC2119] and [@!RFC8174] when, and only when, they appear in all capitals.

All multioctet integers use network byte order. Concatenation means literal
octet concatenation, without separators, alignment, text conversion or
implicit length fields. A tag is a message authentication code (MAC), not a
public-key signature. Key IDs identify locally provisioned keys and are not
credentials or statements of sender identity.

# Object Authentication Header TLV

This experimental draft uses Private Use base type 0x4001 from the RIFP
Header TLV Base Types registry. The Critical bit MUST be set, producing wire
type 0xC001. This is a private-use interoperability convention, not an IANA
allocation; deployments MUST coordinate its use to avoid collisions.

The TLV MUST occur exactly once on every OBJECT_DESCRIPTOR frame belonging
to an authenticated transfer, including each retransmitted descriptor. It
MUST NOT occur on MANIFEST, DATA, END or CANCEL frames. Descriptors for the
same transfer MUST carry byte-identical authentication values. The TLV
Length is 35 + N, where N is the Key ID Length.

| Value offset | Size (octets) | Field |
|--------------|---------------|-------|
| 0 | 1 | Extension Version: 1 |
| 1 | 1 | Algorithm: 1 (HMAC-SHA-256) |
| 2 | 1 | Key ID Length: N, from 1 through 32 |
| 3 | N | Key ID |
| 3 + N | 32 | Authentication Tag |

Key ID consists only of ASCII letters, digits, period, underscore or hyphen.
It is case-sensitive. The value MUST contain exactly the indicated fields;
trailing octets, truncated fields, invalid Key IDs, duplicate TLVs and an
unset Critical bit MUST be rejected. Unsupported extension versions or
algorithms MUST be rejected, not treated as unsigned transfers. Future
algorithms require a separate specification of their key, tag and transcript
semantics; algorithm negotiation is not defined here.

The extension consumes 39 + N header octets including its four-octet TLV
header. Senders MUST respect the core 255-octet header limit when adding
other extensions; they MUST fail rather than silently omit authentication.
No changes to the RIFP major or minor version, frame types, payload layouts,
flags or radio profile are required.

# Keys and Authenticated Transcript

Algorithm 1 uses HMAC with SHA-256 as defined in [@!RFC2104] and [@!RFC6234].
The full 32-octet tag is transmitted without truncation. The pre-shared key
MUST contain exactly 32 uniformly random octets. Passwords MUST NOT be used
directly as keys. Provisioning, rotation and authorization of keys are local
policy and are outside the on-air protocol. The key MUST NOT be transmitted
in a frame or embedded in a manifest. A receiver MUST select a key from
trusted local configuration using the Key ID; it MUST NOT retrieve a key
from a sender-provided URL or file path.

The authenticated transcript M is the following concatenation:

~~~
M = Domain || AuthenticationPrefix || Context || Descriptor

Domain = ASCII("RIFP-OBJECT-AUTH-v1") || 0x00
AuthenticationPrefix = 0x01 || 0x01 || N || KeyID
Context = 0x01 || 0x00 || SessionID || TotalCount
Descriptor = the exact 56-octet OBJECT_DESCRIPTOR payload

Tag = HMAC-SHA-256(PSK, M)
~~~

N is one octet. SessionID is the eight-octet Session ID from the descriptor
frame header. TotalCount is the four-octet Total Count from that header and
MUST be greater than zero. The Context prefix 0x01 0x00 denotes RIFP 1.0
object semantics; it remains fixed for backward-compatible minor-version
additions. A future incompatible object format requires a new extension
version. The AuthenticationPrefix includes the version, algorithm and Key
ID, but excludes the tag itself and the enclosing TLV header.

The descriptor is validated according to [@!RIFP] before use. Its reserved
fields MUST be zero. Its image encoding, pixel format, dimensions, chunk
size, encoded size, CRC-32 and SHA-256 are authenticated byte for byte.
The SHA-256 digest authenticates the complete encoded object indirectly.
Receivers MUST still check the reassembled object's size, CRC-32 and SHA-256
against the authenticated descriptor. A valid tag alone does not establish
that the received DATA payloads are authentic.

Optional JSON MANIFEST contents and other header TLVs, including filename,
creation time, Sender ID, Content Hint and Radio Profile, are NOT
authenticated by this extension. Core consistency checks still apply.
Applications MUST NOT treat those fields as authenticated assertions.
Advisory retransmission flags, individual DATA frames and CANCEL frames are
also outside the MAC; their manipulation can prevent delivery, but cannot
produce a different authenticated image. DATA need not carry the TLV,
allowing existing reordering, repetition and recovery behavior.

# Sender and Receiver Processing

A sender enabling this extension MUST compute the descriptor and chunk count
before calculating the tag. It MUST attach the critical TLV to each
descriptor frame. The existing frame CRC-32 covers the header, including the
authentication TLV, and the payload as specified in [@!RIFP].

A receiver supporting this extension MUST reject an authenticated descriptor
when its key is unavailable, its Key ID is unauthorized, the TLV is
malformed, or verification fails. Tags MUST be compared in constant time.
There MUST NOT be fallback to unsigned processing of that descriptor. Once
an authenticated descriptor is accepted, an unsigned or differently
authenticated descriptor for the same active session MUST cause the session
to be abandoned. A receiver SHOULD retain a bounded failure record until
session expiry so repeated frames cannot immediately recreate an abandoned
authenticated session as unsigned. This record is not a replay cache.

A receiver MAY buffer DATA before the descriptor within core resource limits.
It MUST NOT decode, save, display or otherwise release an authenticated image
until BOTH descriptor MAC verification and complete-object integrity checks
have succeeded. This also applies to saving the encoded payload. A valid
unsigned descriptor MAY be processed only if local policy permits unsigned
objects. Receivers SHOULD clearly distinguish verified images from unsigned
images in their user interface or logs.

Applications requiring authentication MUST reject descriptors without this
extension, independently of whether a signed descriptor has already been
seen. Otherwise, stripping all authentication TLVs and recomputing frame
CRCs allows an attacker to downgrade a transfer to ordinary unsigned RIFP.
Requiring authentication is a receiver policy, not a property inferred from
untrusted traffic.

A receiver without support rejects the unknown critical TLV and therefore
cannot accept the authenticated transfer's mandatory descriptor. Such a
receiver can still process ordinary unsigned RIFP transfers. The critical
extension is optional to implement, but mandatory to understand when present.

# Reference Implementation and Offline Testing

The Python implementation uses only the standard library for cryptography.
Both tools accept `--auth-key-file PATH` for a file containing exactly 32 raw
key bytes, and `--auth-key-id ID` for a public identifier (default `default`).
The receiver additionally accepts `--require-auth`. Without that option,
unsigned transfers remain permitted even when a verification key is loaded.
Authenticated descriptors without a matching configured key are rejected.
In IQ-file mode, `--require-auth` returns a nonzero exit status if no
authenticated image completes.

An example from the repository root, with a securely provisioned PSK file:

~~~
python3 radiofax_sender.py example/input-formats/telefunken-rifp.png \
  --preset small --codec group4 --bits 1 \
  --packet-repeats 1 --manifest-repeats 1 --duty-cycle 1 \
  --auth-key-file /secure/rifp.psk --auth-key-id station-1 \
  --iq-output /tmp/authenticated.cf32

python3 radiofax_receiver.py --iq-input /tmp/authenticated.cf32 \
  --auth-key-file /secure/rifp.psk --auth-key-id station-1 \
  --require-auth --output-dir /tmp/authenticated-images

python3 -m unittest -v test_rifp_auth.py
~~~

Conformance testing covers signed and unsigned IQ loopbacks, valid frame CRCs
on tampered data, recomputed unkeyed image integrity values, wrong or missing
keys, unknown Key IDs, authentication stripping, descriptor/session binding,
malformed and duplicate TLVs, repeated and reordered frames, and legacy
critical-extension rejection. The existing codec/preset fixture tests remain
applicable because authentication does not change image encoding.

# Test Vector

The following public test key MUST NOT be used in deployments. Hexadecimal
strings have no embedded whitespace; displayed line breaks are for readability.

~~~
PSK = 000102030405060708090a0b0c0d0e0f
      101112131415161718191a1b1c1d1e1f
Key ID = "test-key" (8 ASCII octets)
Session ID = 7
Total Count = 1
Encoded image = 78 (one grayscale pixel, value 120)

Descriptor =
01050400000100010001000000000000000000018cdc1683
2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881

TLV Type = c001
TLV Length = 002b
TLV Value =
010108746573742d6b6579
e23c131f627f7d83f5e784d653971c53eff32049da2176dddf2a0fff2ec3d3d3
~~~

The tag has also been checked independently using OpenSSL HMAC-SHA-256.

# Security Considerations

This extension provides image integrity and proof of possession of a shared
key. Any party holding the key, including a receiver, can forge a valid
transfer. It provides neither nonrepudiation nor proof of a unique sender.
Different authorization groups SHOULD use different keys. Keys MUST be stored
with appropriate access controls and rotated following compromise. Key IDs
and image contents remain visible; stable IDs can enable tracking.

Binding the session ID prevents transplantation of a descriptor's tag into a
different session. It does not prevent replay of the complete original
session and image. Deployments needing freshness MUST add an independently
specified replay/freshness mechanism or external policy. Merely generating
random session IDs or retaining sessions temporarily is not replay protection.

An adversary can still jam, delete, reorder or inject frames, spoof cancellation
or modify unauthenticated metadata. Such attacks can deny service. Resource
limits, safe image decoders and all core hostile-input checks remain required.
A MAC authenticates bytes from a key holder, not the safety of decoding them.

No confidentiality, encrypted key exchange, public-key algorithm, certificates
or downgrade-resistant capability negotiation are defined. Authentication
stripping is addressed by explicitly requiring authentication at the receiver.

# IANA Considerations

This document makes no IANA registration request. Base type 0x4001 is in the
core registry's Private Use range. An interoperable public allocation would
require a later revision and the core Specification Required procedure; it
MUST NOT be represented as assigned by this experimental draft.

# References

<reference anchor='RFC2104' target='https://www.rfc-editor.org/info/rfc2104'>
  <front>
    <title>HMAC: Keyed-Hashing for Message Authentication</title>
    <author initials='H.' surname='Krawczyk'/>
    <author initials='M.' surname='Bellare'/>
    <author initials='R.' surname='Canetti'/>
    <date year='1997' month='February'/>
  </front>
  <seriesInfo name='RFC' value='2104'/>
  <seriesInfo name='DOI' value='10.17487/RFC2104'/>
</reference>

<reference anchor='RFC2119' target='https://www.rfc-editor.org/info/rfc2119'>
  <front>
    <title>Key words for use in RFCs to Indicate Requirement Levels</title>
    <author initials='S.' surname='Bradner'/>
    <date year='1997' month='March'/>
  </front>
  <seriesInfo name='BCP' value='14'/>
  <seriesInfo name='RFC' value='2119'/>
  <seriesInfo name='DOI' value='10.17487/RFC2119'/>
</reference>

<reference anchor='RFC6234' target='https://www.rfc-editor.org/info/rfc6234'>
  <front>
    <title>US Secure Hash Algorithms (SHA and SHA-based HMAC and HKDF)</title>
    <author initials='D.' surname='Eastlake 3rd'/>
    <author initials='T.' surname='Hansen'/>
    <date year='2011' month='May'/>
  </front>
  <seriesInfo name='RFC' value='6234'/>
  <seriesInfo name='DOI' value='10.17487/RFC6234'/>
</reference>

<reference anchor='RFC8174' target='https://www.rfc-editor.org/info/rfc8174'>
  <front>
    <title>Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words</title>
    <author initials='B.' surname='Leiba'/>
    <date year='2017' month='May'/>
  </front>
  <seriesInfo name='BCP' value='14'/>
  <seriesInfo name='RFC' value='8174'/>
  <seriesInfo name='DOI' value='10.17487/RFC8174'/>
</reference>

<reference anchor='RIFP' target='https://datatracker.ietf.org/doc/draft-dulaunoy-rifp/'>
  <front>
    <title>Radio Image Framing Protocol (RIFP)</title>
    <author initials='A.' surname='Dulaunoy' fullname='Alexandre Dulaunoy'/>
    <date year='2026' month='July'/>
  </front>
  <seriesInfo name='Internet-Draft' value='draft-dulaunoy-rifp-00'/>
</reference>

{backmatter}
