// table_formatter.js

document.addEventListener("DOMContentLoaded", function () {
    const headers = document.querySelectorAll("#simplifiedLogsTable th");
    headers.forEach(header => {
        header.style.padding = "12px";
        header.style.textAlign = "center";
    });
});
