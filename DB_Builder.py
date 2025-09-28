from mysql.connector import connect, Error
from sys import argv


def connectDB():
    # establish connection to the database using the user account established for the app.
    connection = connect(
        host="localhost",
        user="serv-rem-dev",
        password="password",
        database="service_reminders_app"
    )

    c1 = connection.cursor()
    return connection, c1


def dropAllTables(connection, cursor):
    cursor.execute("DROP TABLE IF EXISTS serviceSchedule")
    cursor.execute("DROP TABLE IF EXISTS vehicles")
    cursor.execute("DROP TABLE IF EXISTS users")
    connection.commit()


# *** Table Creation ***#
def createTables(connection, cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            userID INT AUTO_INCREMENT PRIMARY KEY NOT NULL,
            username VARCHAR(255) NOT NULL,
            phone VARCHAR(32)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vehicles (
            vehicleID INT AUTO_INCREMENT NOT NULL,
            userID INT NOT NULL,
            vehNickname VARCHAR(255),
            make VARCHAR(255),
            model VARCHAR(255),
            year YEAR,
            miles DECIMAL(8,1) DEFAULT NULL CHECK (miles >= 0),
            dateLastODO DATE DEFAULT NULL,
            milesPerDay DOUBLE,
            estMiles DOUBLE,
            PRIMARY KEY (vehicleID),
            FOREIGN KEY (userID) REFERENCES users(userID)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS serviceSchedule (
            itemID INT AUTO_INCREMENT NOT NULL,
            vehicleID INT NOT NULL,
            userID INT NOT NULL,
            description LONGTEXT NOT NULL,
            serviceInterval INT NOT NULL CHECK (serviceInterval > 0),
            dueAtMiles DECIMAL(8,1) CHECK (dueAtMiles >= 0),
            servDueFlag BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (itemID),
            FOREIGN KEY (vehicleID) REFERENCES vehicles(vehicleID),
            FOREIGN KEY (userID) REFERENCES users(userID)
        )
    """)

    connection.commit()


# *** Sample Data ***#
def loadSampleData(connection, cursor):
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
    cursor.executemany(sampleUsersStatement, sampleUsers)

    sampleVehiclesStatement = """
        INSERT INTO vehicles (userID, vehNickname, make, model, year, miles, dateLastODO, milesPerDay, estMiles)
        VALUES ( %s, %s, %s, %s, %s, %s, %s, %s, %s )
    """
    sampleVehicles = [
        (1, "Moose", "Lexus", "Rx350", "2015",
         "110000", "2025-9-13", "20.3", "110020.3"),
        (1, "Yoda", "Toyota", "Rav4", "2011", "125920", "2025-9-13", "100.4", "0"),
        (2, None, "Subaru", "Crosstrek", "2019",
         "10", "2025-9-10", "200.1", "210.1"),
        (3, None, "Subaru", "Outback", "2025", None, None, None, None),
        (4, "Grandma", "Volkwagen", "Jetta TDI Sportwagen",
         "2014", "140020", "2024-7-13", "234", "10"),
        (4, "Grandpa", "Subaru", "Forester",
         "2005", "250120", "2025-09-11", None, "534")
    ]
    cursor.executemany(sampleVehiclesStatement, sampleVehicles)

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
        (5, 4, "flush brakes", 2, 100),
        (6, 4, "set alignmnet", 10, 1029000)
    ]
    cursor.executemany(sampleServSchedStmt, sampleServiceSched)

    # commit changes
    connection.commit()


# class to handle connecting and disconnecting from the databsase using with...as statements.
class DBConnection:
    def __init__(self):
        self.connection = None
        self.cursor = None

    def __enter__(self):
        # establish connection to the database using the user account established for the app.
        self.connection = connect(
            host="localhost",
            user="serv-rem-dev",
            password="password",
            database="service_reminders_app"
        )

        self.cursor = self.connection.cursor()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            print(
                f"An excaption occurred: {exc_type.__name__}: {exc_val}. Traceback: {exc_tb}")
            self.connection.rollback()
        else:
            self.connection.commit()

        self.cursor.close()
        self.connection.close()


def newDBWithData():
    with DBConnection() as db:
        con = db.connection
        curs = db.cursor
        dropAllTables(con, curs)
        createTables(con, curs)
        loadSampleData(con, curs)


if __name__ == "__main__":
    newDBWithData()
