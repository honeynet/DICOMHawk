document.addEventListener('DOMContentLoaded', function(){
    function updateStatus(){
        fetch('/status',{
            headers: {
                'Accept': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => {
            document.getElementById('server-status').textContent = data.status;
            const timestamp = data.last_updated.replace('T', ' ').split('.')[0];
            document.getElementById('status-timestamp').textContent = 'Last Updated: ' + timestamp;
        });
    }
    updateStatus();
    setInterval(updateStatus, 30000);
})