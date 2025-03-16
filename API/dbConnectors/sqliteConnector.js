//db
const sqlite3 = require('sqlite3').verbose();
var databasePath = "./../dicom_server/storage/db.db"
if (process.env.Docker_ENV=="True") {
    databasePath = '/app/db.db'
}

// Connect to the SQLite database
function connectToDB() {
    const db = new sqlite3.Database(databasePath, sqlite3.OPEN_READWRITE | sqlite3.OPEN_CREATE, (err) => {
        if (err) {
            console.error('Error opening database:', err.message);
        } else {
            console.log('Connected to the SQLite database.');
        }
    });

    db.serialize(() => {
        db.each("SELECT name FROM sqlite_master WHERE type='table'", (err, table) => {
            if (err) {
                console.error('Error running query', err.message);
                return;
            }
            // console.log(table.name);
        });
    });


    return db;
}


module.exports = {
    connectToDB
};
