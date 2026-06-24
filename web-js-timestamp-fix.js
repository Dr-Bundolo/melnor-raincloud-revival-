// Fix for: RF pairing to the valve unit silently drops after 1-2 hours,
// even though the WebSocket connection and "online" status stay healthy.
//
// Root cause: the original code only sends msgTimestamp() once, during
// initial connection setup. The CU's firmware appears to depend on
// periodic timestamp updates to keep its RF link with the valve unit
// alive, independent of the WebSocket connection itself.
//
// Fix: add msgTimestamp(timeStamp) inside checkTimeout(), so it goes out
// every 60 seconds alongside the existing watchdog/ping logic.

function checkTimeout() {
    let dbg = '';
    timeStamp += 1;
    weblog.debug(`Watchdog : time:${timeStamp}/${remoteStamp}`);
    for (let i = 0; i < valves.length; i++) {
        const t = parseInt(valves[i], 10);
        if (t > timeStamp) {
            dbg += `V${i}:${t - timeStamp} `;
        } else {
            dbg += `V${i}:OFF `;
            valves[i] = 0;
        }
    }
    weblog.debug(`VALVES : ${dbg}`);
    msgTimestamp(timeStamp);  // <-- ADDED: send the updated timestamp every cycle
    sendPing(wss.clients);
}

// IMPORTANT: once the server is sending its own timestamp periodically like
// this, it becomes the authoritative clock. You must also REMOVE the two
// lines elsewhere in the file that read:
//
//     timeStamp = remoteStamp;
//
// (one appears in updateStates(), the other in the device-registration
// /submit handler). Leaving those in place lets the CU's own reported
// clock overwrite your server's timestamp, which can desync valve-duration
// tracking if you're also driving a pump or other device based on valve
// state.
