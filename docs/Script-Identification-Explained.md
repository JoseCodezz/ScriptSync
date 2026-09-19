How AI Agent Identity Works (Explained Simply)
----------------------------------------------
Imagine you get an email from an AI assistant claiming to represent Acme Corp. How do you know it's actually authorized by Acme Corp, and not some random person pretending?

This project answers that question using something every company already has: their website domain. If a company owns acme.com, they can prove that ownership to create a trustworthy ID card for their AI agents.

Think of it like a company badge system. The company (the domain owner) is the only one who can issue badges, and anyone can check a badge is real just by asking the company's own front desk (their DNS records).
----------------------------------------------
The four ingredients
1. A key pair (like a lock and its one matching key)
When a company registers an agent, our server creates two mathematically linked pieces:

A private key — kept secret, given only to the agent. This is the "key."
A public key — shared with the world. This is the "lock."
Anything locked with the public key can only be opened by the matching private key, and vice versa. This is standard, well-tested cryptography (called Ed25519) — the same category of math that protects your online banking.

Important: the private key is shown to the company exactly once, at creation time. Our server does not keep a copy. If it's lost, it can't be recovered — a new one has to be issued.

2. Publishing the "lock" publicly (DNS)
The company then publishes their agent's public key in their own DNS records — specifically, a small note called a TXT record, at an address like:

_agentid.support-agent.acme.com
DNS is the system that translates web addresses (like acme.com) into the technical info that makes the internet work. It's already public, already trusted, and only the domain's actual owner can edit it (through their registrar, e.g. GoDaddy). That's what makes it a good place to publish an agent's identity — nobody else can plant a fake entry there.

This is the equivalent of the company's front desk holding a directory of every valid employee badge, and letting anyone walk up and check the directory.

3. Proving the agent has the key (not just claiming it)
Publishing the public key isn't enough on its own — anyone could still just claim "I'm the acme.com support agent" without actually having the private key. So we need the agent to prove it.

Here's the process, step by step:

A verifier asks a question. Anyone who wants to check the agent's identity requests a random, one-time "challenge" — think of it as a verifier saying, "if you're really who you say you are, write down this exact random word for me."
The agent signs it. The agent uses its private key to create a signature over that exact challenge. A signature here isn't handwriting — it's a piece of math that could only have been produced by someone holding the private key.
The verifier checks it. The verifier looks up the public key from DNS (step 2 above) and uses it to check whether the signature is genuine.
If the signature checks out, the verifier now knows two things at once:

The domain (acme.com) really did publish this public key, since it came straight from their DNS.
Whoever signed the challenge really does hold the matching private key.
Both of those together are what prove the agent is legitimately authorized by that domain.

4. Why the "one-time random word" matters
If the agent always signed the same fixed message, anyone who ever saw one valid signature could just replay it forever to fake being the agent — like photocopying someone's signature off an old letter and pasting it onto a new document.

By making the verifier pick a brand-new random challenge every single time, and only accepting each one once, old signatures become useless. This is what stops replay attacks.

A quick analogy for the whole thing
Real world	This system
A company's HQ address	The domain (acme.com)
A badge with a photo	The agent's public key, published in DNS
The badge holder's actual fingerprint	The agent's private key
Front desk directory anyone can check	Public DNS records
"Please sign this randomly chosen form to prove it's really you"	The challenge-and-signature step
A photocopied signature reused later (fraud)	A replayed old signature (blocked by one-time challenges)
Why this is stronger than just "trust me"
Without this system, there's no reliable way to know an AI agent is really speaking for the company it claims to represent — you'd just be taking its word for it. With this system:

Only the real domain owner can publish the "badge" (DNS access is protected by the registrar).
Only someone holding the actual private key can produce a valid signature.
Old signatures can't be reused to fake a new conversation.
None of this requires trusting a middleman or a central authority — it relies on domain ownership (something companies already control) and well-established cryptography (something mathematicians and security researchers have stress-tested for decades).

Questions or want the technical version with real code? Ask and it can be provided alongside this plain-language explanation.