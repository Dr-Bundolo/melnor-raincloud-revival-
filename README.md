# Reviving a Melnor RainCloud After Melnor Discontinued the Cloud Service

If you own a Melnor RainCloud smart water timer and discovered it stopped working when Melnor shut down their cloud backend, you're not stuck with a dead piece of hardware. The Control Unit and Valve Unit still work fine — they just need something to talk to instead of Melnor's defunct servers. A Raspberry Pi (even an older Zero W) can fill that role.

## How it works

The Control Unit was designed to connect to two of Melnor's cloud services over plain, unencrypted HTTP — a WebSocket connection (built on Pusher) for receiving commands, and periodic HTTP requests reporting its own status. Because none of this is encrypted or authenticated against a specific server identity, a local device on your network can impersonate those services, and the Control Unit has no way to tell the difference.

## Existing open-source projects (start here)

You don't need to reverse-engineer anything yourself — this groundwork is already done:

- **[sunshower](https://github.com/jpjodoin/sunshower)** by jpjodoin
- **[melnor_decloudify](https://github.com/FreshXOpenSource/melnor_decloudify)** by FreshXOpenSource

Both take the same basic approach: a local server on your network impersonates Melnor's cloud, your router/Pi redirects the Control Unit's DNS lookups to that local server instead of the real internet, and the CU continues operating exactly as designed, just talking to your Pi instead.

## What you'll need

- A Raspberry Pi (even low-power models work — this has been running reliably on a Pi Zero W)
- Your device's ID (a 12-character code printed on the Control Unit)
- Basic comfort with SSH/Linux and your router's DHCP/DNS settings

## Known issues and fixes (found during real-world deployment)

**RF pairing silently drops after 1-2 hours.** The Control Unit shows "online" the entire time and the WebSocket connection stays healthy, but commands stop actually reaching the valve. Root cause: the server only sends a timestamp sync to the CU once, during initial connection — never again. The CU's firmware appears to depend on periodic timestamp updates to keep its RF link with the valve unit alive, separate from the WebSocket connection itself. Fix: send a timestamp update every ~60 seconds, alongside any periodic ping/keepalive logic already in place.

**If you're also bridging to a separate pump or smart-home platform:** make sure your own code treats your Pi's
timestamp as authoritative once you start sending it periodically — don't let the CU's own reported clock overwrite it, or you can get desynced valve-duration tracking (a pump might shut off before the valve's commanded duration actually elapses, or vice versa).

**Both fixes have been running in production for several days** across manual, direct API, and fully scheduled/unattended runs, with the RF link holding for many consecutive hours where it previously failed within 1-2.

## Credit

This builds entirely on the reverse-engineering work of jpjodoin and FreshXOpenSource — without their projects, none of this would be possible. This guide just documents some additional issues found running one of these setups long-term, unattended, at a remote property.
