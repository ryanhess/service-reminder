from mysql.connector import connect, Error

# establish connection to the database using the user account established for the app.
connection = connect(
    host="localhost",
    user="serv-rem-dev",
    password="password",
    database="service_reminders_app"
)

c1 = connection.cursor()

# delete everything that will here now be created.
c1.execute("DROP TABLE IF EXISTS serviceSchedule")
c1.execute("DROP TABLE IF EXISTS vehicles")
c1.execute("DROP TABLE IF EXISTS users")
c1.execute("DROP PROCEDURE IF EXISTS service_reminders_app.setServiceReminderFlags")
connection.commit()

# *** Table Creation ***#
c1.execute("""
    CREATE TABLE IF NOT EXISTS users (
        userID INT AUTO_INCREMENT PRIMARY KEY NOT NULL,
        username VARCHAR(255) NOT NULL,
        phone VARCHAR(32)
    )
""")

c1.execute("""
    CREATE TABLE IF NOT EXISTS vehicles (
        vehicleID INT AUTO_INCREMENT NOT NULL,
        userID INT NOT NULL,
        vehNickname VARCHAR(255),
        make VARCHAR(255),
        model VARCHAR(255),
        year YEAR,
        miles DECIMAL(8,1) CHECK (miles >= 0),
        dateLastODO DATE,
        milesPerDay DOUBLE,
        estMiles DOUBLE,
        PRIMARY KEY (vehicleID),
        FOREIGN KEY (userID) REFERENCES users(userID)
    )
""")

c1.execute("""
    CREATE TABLE IF NOT EXISTS serviceSchedule (
        itemID INT AUTO_INCREMENT NOT NULL,
        vehicleID INT NOT NULL,
        userID INT NOT NULL,
        description LONGTEXT NOT NULL,
        serviceInterval INT NOT NULL,
        dueAtMiles DECIMAL(8,1),
        servDueFlag BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (itemID),
        FOREIGN KEY (vehicleID) REFERENCES vehicles(vehicleID),
        FOREIGN KEY (userID) REFERENCES users(userID)
    )
""")

# *** Procedure Creation ***#

# for all vehicles, Calculate the estimated odometer for today and if that number is within 500 miles of a service deadline, set a flag on that service item.
# select itemID from service schedule where for the vehicle for that schedule, the todayODOD is
"""
CREATE PROCEDURE service_reminders_app.setServiceReminderFlags()
    BEGIN 
        
        #for each vehicle, calc the todays odo estimate and store it.
        #for each service item, if deadline-odoEst < some constant, set the flag.
    END
"""


# *** Sample Data ***#
sampleUsersStatement = """
    INSERT INTO users
    (username, phone)
    VALUES ( %s, %s )
"""
sampleUsers = [
    ("ryanhess", "+18777804236"),
    ("stephenhess", "+16469576453"),
    ("brianhess", "+19177978174"),
    ("sorayahess", "+19178487133")
]
c1.executemany(sampleUsersStatement, sampleUsers)

sampleVehiclesStatement = """
    INSERT INTO vehicles (userID, vehNickname, make, model, year, miles, dateLastODO, milesPerDay)
    VALUES ( %s, %s, %s, %s, %s, %s, %s, %s )
"""
sampleVehicles = [
    ("1", "Moose", "Lexus", "Rx350", "2015", "110000", "2025-9-13", "20.3"),
    ("1", "Yoda", "Toyota", "Rav4", "2011", "125920", "2025-8-13", "100.4"),
    ("2", None, "Subaru", "Crosstrek", "2019", "10", "2025-9-10", "200.1"),
    ("3", None, "Subaru", "Outback", "2025", None, None, None),
    ("4", "Grandma", "Volkwagen", "Jetta TDI Sportwagen",
     "2014", "140020", "2024-7-13", "234"),
    ("4", "Grandpa", "Subaru", "Forester", "2005", "250120", "2025-09-11", None)
]
c1.executemany(sampleVehiclesStatement, sampleVehicles)

sampleServSchedStmt = """
    INSERT INTO serviceSchedule (vehicleID, userID, description, serviceInterval, dueAtMiles)
    VALUES ( %s, %s, %s, %s, %s )
"""
sampleServiceSched = [
    (1, 1, "Change Eng. Oil and Filter", 5000, 110300),
    (1, 1, "Rotate and Inspect Tires", 5000, 110300),
    (1, 1, "Re-torque drive shaft bolts", 15000, 120000),
    (2, 1, "Change Eng. Oil and Filter", 5000, 130000),
    (2, 1, "Replace Brake Fluid", 10000, 126000),
    (3, 2, "Change tires", 1, 0),
    (4, 3, "change oil", 1, 6000),
    (5, 4, "flush brakes", 0, 100),
    (6, 4, "set alignmnet", 10, 1029000)
]
c1.executemany(sampleServSchedStmt, sampleServiceSched)

# commit changes
connection.commit()

# close open connections and cursors
c1.close()
connection.close()
