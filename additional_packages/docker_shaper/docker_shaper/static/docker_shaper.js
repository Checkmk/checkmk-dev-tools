"use strict";

console.info("Hello from docker_shaper.js");

/// Automatically exectuted main function
(function() {
    function startControlSocket() {
        const controlSocket = new WebSocket(new URL(
            "control", window.location).href.replace(/^http/, 'ws'));

        controlSocket.onopen = () => {
            console.info("WebSocket connection is open");
        };

        controlSocket.onmessage = (event) => {
            console.debug("received message:", event.data);
            if (event.data == "refresh") {
                controlSocket.send("ok");
                location.reload();
            } else {
                console.warning(`got unknown message ${event.data}`);
                controlSocket.send("nok - unknown");
            }
        };

        controlSocket.onclose = function(){
            console.info("controlSocket closed - reconnect in a second");
            setTimeout(startControlSocket, 1000);
        }
    }

    startControlSocket();
})();

