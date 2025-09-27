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
    with DBConnection as db:
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


def test_promptUserForOneVeh():
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


def test_updateODO():
    # needs to test that the function performs the expected result which is:
    #   vehicleID's ODO value is updated to the input value
    # raises NotInDatabaseError when vehicle is not in the database
    # raises ValueError if the inputted miles are less than the ODO value on record.

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


def test_updateServiceDone():
    # check that the service-due-flag is now false.
    # check that the mileage deadline is now extended by the mileage interval plus the odo value
    # check that when odo is less than parent miles, the parent miles is not updated.
    # check proper NotInDatabaseError.
    def runTest(id, odo):
        with DBConnection() as db:
            curs = db.cursor

            # Pass any raised exceptions out to the caller.
            try:
                main.updateServiceDone(itemID=id, odo=odo)
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
            assert float(dueAt) == odo + float(interval)

            # should normally be true, should be false when odo is less than original parentMiles
            return parentMiles < odo

    def populateDB():
        # we need a sample database with a vehicle and a few service items with true flags and one with a false flag.
        with DBConnection as db:
            cur = db.cursor

            sampleServSchedStmt = """
                INSERT INTO serviceSchedule (vehicleID, userID, description, serviceInterval, dueAtMiles, servDueFlag)
                VALUES ( %s, %s, %s, %s, %s )
            """

            sampleServiceSched = [
                (1, 1, "Change Eng. Oil and Filter", 5000, 11030, True),
                (1, 1, "Rotate and Inspect Tires", 5000, 110300, True),
                (1, 1, "Re-torque drive shaft bolts", 15000, 120000, True),
                (2, 1, "Change Eng. Oil and Filter", 5000, 130000, True),
                (2, 1, "Replace Brake Fluid", 10000, 126000, True),
                (3, 2, "Change tires", 1, 0, False),
                (4, 3, "change oil", 1, 6000, False),
                (5, 4, "flush brakes", 0, 100, True),
                (6, 4, "set alignmnet", 10, 1029000, False)
            ]

            cur.executemany(sampleServSchedStmt, sampleServiceSched)

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

    buildBlankDB()
    populateDB()

    # check for not in database
    with raises(main.NotInDatabaseError):
        runTest(0, 0)

    # check if a mileage from the past is introduced here it will not be the mileae fr teh vehcile.

    # check the rest of the requirements with a few service items.
    runTest()
