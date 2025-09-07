from mysql.connector import connect, Error

#establish connection to the database using the user account established for the app.
connection = connect(
    host = "localhost",
    user = "serv-rem-dev",
    password = "password",
    database = "service_reminders_app"
)

c1 = connection.cursor()

#formulate the queries to create the table schemas
createUsersTableQuery = """
    CREATE TABLE IF NOT EXISTS users (
        userID INT AUTO_INCREMENT PRIMARY KEY NOT NULL,
        username VARCHAR(255) NOT NULL,
        phone BIGINT CHECK (phone >= 0)
    )
"""

createVehiclesTableQuery = """
    CREATE TABLE IF NOT EXISTS vehicles (
        vehicleID INT AUTO_INCREMENT NOT NULL,
        userID INT NOT NULL,
        make VARCHAR(255),
        model VARCHAR(255),
        year VARCHAR(4),
        miles DECIMAL(8,1) CHECK (miles >= 0) DEFAULT null,
        milesPerDayMovingAverage DOUBLE DEFAULT null,
        serviceSchedule JSON,
        PRIMARY KEY (vehicleID),
        FOREIGN KEY (userID) REFERENCES users(userID)
    )
"""

#execute the create table statements
c1.execute(createUsersTableQuery)
c1.execute(createVehiclesTableQuery)

#add sample data to tables, first users then vehicles.
sampleUsersStatement = """
    INSERT INTO users
    (username, phone)
    VALUES ( %s, %s )
"""
sampleUsers = [
    ("ryanhess", "6469576452"),
    ("stephenhess", "6469576453"),
    ("brianhess", "9177978174"),
    ("sorayahess", "9178487133")
]

sampleVehiclesStatement = """
    INSERT INTO vehicles (userID, make, model, year)
    VALUES ( %s, %s, %s, %s )
"""
sampleVehicles = [
    ("1", "Lexus", "Rx350", "2015"),
    ("1", "Toyota", "Rav4", "2011"),
    ("2", "Subaru", "Crosstrek", "2019"),
    ("3", "Subaru", "Outback", "2025"),
    ("4", "Volkwagen", "Jetta TDI Sportwagen", "2014"),
    ("4", "Subaru", "Forester", "2005")
]

#add values to tables
c1.executemany(sampleUsersStatement, sampleUsers)
c1.executemany(sampleVehiclesStatement, sampleVehicles)

#commit changes
connection.commit()

#close open connections and cursors
c1.close()
connection.close()