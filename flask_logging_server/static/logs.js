document.addEventListener("DOMContentLoaded", function () {
    function fetchLogs() {
        fetch('/logs/simplified')
            .then(response => response.json())
            .then(logs => {
                const tbody = document.querySelector("#logs-table tbody");
                tbody.innerHTML = ''; // Clear existing logs
                let connectionCount = 0; // Initialize connection count

                // Function to add a connection header
                function addConnectionHeader() {
                    connectionCount++; // Increment connection count on new header
                    const cell = document.createElement('td');
                    cell.setAttribute('colspan', '11');
                    cell.style.fontWeight = 'bold';
                    cell.textContent = `Connection ${connectionCount}`;
                    tbody.appendChild(cell);
                }

                logs.forEach((log, index) => {
                    // Add connection header on first log or when 'Association Requested' is found
                    if (index === 0 || log.request_type === "Association Requested") {
                        addConnectionHeader();
                    }

                    const row = document.createElement("tr");
                    const logFields = [
                        log.session_id, log.ip, log.port, log.version, log.request_type,
                        log.session_parameters, log.matches, log.status, log.log_level,
                        log.query_level, log.timestamp
                    ];

                    // Create and append cells for each field
                    logFields.forEach(field => {
                        const cell = document.createElement("td");
                        cell.textContent = field || "N/A";
                        row.appendChild(cell);
                    });

                    tbody.appendChild(row);
                });

            })
            .catch(error => console.error('Error fetching simplified logs:', error));
    }

    // Fetch logs initially and set interval to fetch logs every 5 seconds
    fetchLogs();
    setInterval(fetchLogs, 5000);
});
