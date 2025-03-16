document.addEventListener('DOMContentLoaded', () => {
    fetch('/status')
        .then(response => response.json())
        .then(data => {
            const statusDiv = document.getElementById('status');
            statusDiv.textContent = `Associations: ${data.associations}, Status: ${data.status}`;
        });

    fetch('/simplified_logs.log')
        .then(response => response.text())
        .then(data => {
            const logsPre = document.getElementById('logs');
            logsPre.textContent = data;
        });
});


// document.addEventListener('DOMContentLoaded', function () {
//     console.log("Logs page loaded.");
// });
