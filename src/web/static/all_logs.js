document.addEventListener("DOMContentLoaded", function () {
    function fetchLogs() {
        fetch('/logs/all')
            .then(response => response.text())
            .then(data => {
                document.getElementById('logs').innerHTML = data;
            })
            .catch(error => console.error('Error fetching logs:', error));
    }

    // Fetch logs initially
    fetchLogs();

    // Set interval to fetch logs every 5 seconds
    setInterval(fetchLogs, 5000);
});
