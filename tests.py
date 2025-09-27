import main
import DB_Builder
from DB_Builder import DBConnection
from mysql.connector import connect, Error
from pytest import fixture, raises
from decimal import Decimal
import pytest_mock


@fixture
def client():
    main.app.config.update({"TESTING": True})

    with main.app.test_client() as client:
        yield client


### HELPERS ###
def buildSampleDB():
    DB_Builder.newDBWithData()


def buildBlankDB():
    with DBConnection() as db:
        con = db.connection
        curs = db.cursor
        DB_Builder.dropAllTables(con, curs)
        DB_Builder.createTables(con, curs)

### TESTS ###


def test_homepage(client):
    response = client.get("/")
    assert response.status_code == 200


def test_querySQL():
    # NEEDS NO TEST
    # querySQL:
    #   passes on SQL queries to the database using the mysql connector python library.
    #   passes back any errors.
    #   opens and then closes the connection, through with/as statement.
    #   This is all using other people's code. There is nothing I wrote that I need to test.
    return


# promptUserForOneVeh does:
#   -retrieves one row from the vehicles table that belongs to the given user,
#    requires a ODO reading, and is the most out of date vehicle with such requirement.
#   -queries the users row for that user ID.
#   -extracts the user's phone number and composes a message out of all the info.
#   -returns the message and phone number.
#
# edge/other cases to test:
#   -function returns expected result for some case.
#   -usrID is not in the database.
#       -this is not a critical error and should not crash the server, but should make a console message.
#       -test the return values.
#   -the given user has more than one veh with the same dateLastODO. Function should execute and return one of the two possible results.
def test_promptUserForOneVeh():
    buildSampleDB()  # rebuild the database with some sample data.
    # test user 4. should return data "hey Soraya," "Grandma" stuff stuff.
    phone, msg = main.promptUserForOneVeh(usrID=4)
    assert phone == "+19178487133"
    assert "sorayah" in msg and "Grandma" in msg

    # test user 3. should return Hey brianhess, please reply with ... your 2025 subaru outback.
    phone, msg = main.promptUserForOneVeh(usrID=3)
    assert phone == "+19177978174"
    assert "brianhess" in msg and "2025 Subaru Outback" in msg

    # test user 1000. should raise a custom expression
    with raises(main.NotInDatabaseError):
        main.promptUserForOneVeh(usrID=1000)

    # test user 0. same result.
    with raises(main.NotInDatabaseError):
        main.promptUserForOneVeh(usrID=0)

    # test user 1. should return one or the other of "ryanhess" and "moose" or "yoda"
    phone, msg = main.promptUserForOneVeh(usrID=1)
    assert phone == "+18777804236"
    assert "ryanhess" in msg and (("Moose" in msg) != ("Yoda" in msg))


# needs to test that the function performs the expected result which is:
#   vehicleID's ODO value is updated to the input value
# raises NotInDatabaseError when vehicle is not in the database
# raises ValueError if the inputted miles are less than the ODO value on record.
def test_updateODO():
    buildSampleDB()

    def runTest(id, odo):
        with DBConnection() as db:
            curs = db.cursor

            # pass out any exceptions
            try:
                main.updateODO(vehID=id, odo=odo)
            except:
                raise

            curs.execute(
                f"SELECT miles FROM vehicles WHERE vehicleID = {id}")
            res = curs.fetchall()
            assert float(res[0][0]) == odo

    runTest(1, 110000.1)
    runTest(2, 1030001)

    with raises(main.NotInDatabaseError):
        runTest(id=0, odo=0)

    with raises(ValueError):
        runTest(id=1, odo=1)


# check that the service-due-flag is now false.
# check that the mileage deadline is now extended by the mileage interval plus the odo value
# check that when odo is less than parent miles, the parent miles is not updated.
# check proper NotInDatabaseError.
# this function should return True if parent miles (after db operations) is greater than the odo passed.
# in oher words, the DB integrity is preserved and the odo values is rejected.
def test_updateServiceDone():
    def runTest(id, odo):
        with DBConnection() as db:
            curs = db.cursor

            # Pass any raised exceptions out to the caller.
            try:
                main.updateServiceDone(itemID=id, servODO=odo)
            except:
                raise

            curs.execute(
                f"SELECT vehicleID, serviceInterval, dueAtMiles, servDueFlag FROM serviceSchedule WHERE itemID = {id}")
            res = curs.fetchall()
            veh, interval, dueAt, flag = res[0]
            curs.execute(f"SELECT miles FROM vehicles WHERE vehicleID = {veh}")
            res = curs.fetchall()
            parentMiles = float(res[0][0])
            assert not flag
            assert float(dueAt) == round(odo, 1) + float(interval)

            # should normally be true, should be false when odo is less than original parentMiles
            return round(odo, 1) == parentMiles

    def populateDB():
        # we need a sample database with a vehicle and a few service items with true flags and some with a false flag.
        with DBConnection() as db:
            cur = db.cursor

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
            cur.executemany(sampleUsersStatement, sampleUsers)

            sampleVehiclesStatement = """
                INSERT INTO vehicles (userID, vehNickname, make, model, year, miles, dateLastODO, milesPerDay)
                VALUES ( %s, %s, %s, %s, %s, %s, %s, %s )
            """
            sampleVehicles = [
                (1, "Moose", "Lexus", "Rx350", "2015",
                 "110000", "2025-9-13", "20.3"),
                (1, "Yoda", "Toyota", "Rav4", "2011",
                 "125920", "2025-9-13", "100.4"),
                (2, None, "Subaru", "Crosstrek",
                 "2019", "10", "2025-9-10", "200.1"),
                (3, None, "Subaru", "Outback", "2025", None, None, None),
                (4, "Grandma", "Volkwagen", "Jetta TDI Sportwagen",
                 "2014", "140020", "2024-7-13", "234"),
                (4, "Grandpa", "Subaru", "Forester",
                 "2005", "250120", "2025-09-11", None)
            ]
            cur.executemany(sampleVehiclesStatement, sampleVehicles)

            sampleServSchedStmt = """
                INSERT INTO serviceSchedule (vehicleID, userID, description, serviceInterval, dueAtMiles, servDueFlag)
                VALUES ( %s, %s, %s, %s, %s, %s )
            """

            sampleServiceSched = [
                (1, 1, "Change Eng. Oil and Filter", 5000, 11030, True),
                (1, 1, "Rotate and Inspect Tires", 5000, 110300, True),
                (1, 1, "Re-torque drive shaft bolts", 15000, 120000, True),
                (2, 1, "Change Eng. Oil and Filter", 5000, 130000, True),
                (2, 1, "Replace Brake Fluid", 10000, 126000, True),
                (3, 2, "Change tires", 1, 0, False),
                (4, 3, "change oil", 1, 6000, False),
                (5, 4, "flush brakes", 2, 100, True),
                (6, 4, "set alignmnet", 10, 1029000, False)
            ]

            cur.executemany(sampleServSchedStmt, sampleServiceSched)

    buildBlankDB()
    populateDB()

    # check for not in database
    with raises(main.NotInDatabaseError):
        runTest(0, 0)
    with raises(main.NotInDatabaseError):
        runTest(9999, 0)

    # Check that the parent miles are NOT updated when
    # odo is less than the original parent miles.
    assert not runTest(1, 11031)
    assert not runTest(2, 100000)

    # check the rest of the requirements with a few service items.
    # (runTest returns true when the parent miles is updated to the rounded odo)
    assert runTest(3, 120000.126)
    assert runTest(4, 125921.00001)
    assert runTest(5, 140000.2)
    assert runTest(6, 100)
    assert runTest(7, 6000)


# should return the string of the message appropriate for the given item.
# independently find the data that should be in the message and compare this to the message.
# should return a "not in database error" if the item is not found.
def test_notifyOneService():
    def runTest(id):
        with DBConnection() as db:
            curs = db.cursor

            # Pass any raised exceptions out to the caller.
            try:
                returnedPhone, returnedMsg = main.notifyOneService(id)
            except:
                raise

            curs.execute(f"""
                SELECT userID, vehicleID, description, dueAtMiles FROM serviceSchedule
                WHERE itemID = {id}
            """)
            res = curs.fetchall()
            usrID, vehID, desc, dueAt = res[0]

            curs.execute(f"""
                SELECT username, phone FROM users
                WHERE userID = {usrID}
            """)
            res = curs.fetchall()
            username, phone = res[0]

            curs.execute(f"""
                SELECT vehNickname, year, make, model FROM vehicles
                WHERE vehicleID = {vehID}
            """)
            res = curs.fetchall()
            nick, year, make, model = res[0]

            assert phone == returnedPhone
            assert username in returnedMsg
            if not nick:
                assert str(
                    year) in returnedMsg and make in returnedMsg and model in returnedMsg
            else:
                assert nick in returnedMsg
            assert desc in returnedMsg

    buildSampleDB()

    with raises(main.NotInDatabaseError):
        runTest(0)

    # just get all the service items in the sample schedule and test them all.
    with DBConnection() as db:
        c = db.cursor
        c.execute("""
            SELECT itemID FROM serviceSchedule
        """)
        ids = c.fetchall()

    for id in ids:
        runTest(id[0])


# test_notifyOneService()
