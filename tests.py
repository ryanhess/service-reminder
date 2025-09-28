import main
import DB_Builder
from DB_Builder import DBConnection
from mysql.connector import connect, Error
from pytest import fixture, raises
from decimal import Decimal
import pytest_mock
from datetime import date, timedelta
from twilio.twiml.messaging_response import MessagingResponse
from contextlib import nullcontext as does_not_raise


@fixture
def client():
    main.app.config.update({"TESTING": True})

    with main.app.test_client() as client:
        yield client


### HELPERS ###

# pretty much a dead ringer for querySQL except it uses my context class DBConnection
# and it does away with the many jazz. fix this multiple mess later.
def querySQLinTest(stmt=""):
    try:
        with DBConnection() as db:
            connection = db.connection
            c1 = db.cursor
            c1.execute(stmt)
            result = c1.fetchall()
            connection.commit()

            return result

    except Error as e:
        raise Exception(e)


def buildSampleDB():
    DB_Builder.newDBWithData()


def buildBlankDB():
    with DBConnection() as db:
        con = db.connection
        curs = db.cursor
        DB_Builder.dropAllTables(con, curs)
        DB_Builder.createTables(con, curs)


### TESTS ###
def test_getDateToday():
    assert main.getDateTodayStr() == date.today().strftime('%Y-%m-%d')


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
#   updates the dateLastODO
#   updates the milesPerDay to the correct value.
# raises NotInDatabaseError when vehicle is not in the database
# raises ValueError if the inputted miles are less than the ODO value on record.
def test_updateODO(mocker):
    buildSampleDB()

    sampleToday = date(2025, 9, 13)  # artificially set today
    mocker.patch('main.getDateToday', return_value=sampleToday)

    def runTest(id, testODO):
        with DBConnection() as db:
            curs = db.cursor

            # get the previous dateLastODO so we can compare.
            curs.execute(
                f"SELECT miles, dateLastODO, milesPerDay FROM vehicles WHERE vehicleID = {id}")

            # just dump to a variable for now to postpone any issues with NotInDatabaseError.
            oldData = curs.fetchall()

        # pass out any exceptions and execute the function
        try:
            main.updateODO(vehID=id, newODO=testODO)
        except:
            raise

        # now unpack with indexing since we made it here.
        oldODO, oldDateLast, oldMilesPerDay = oldData[0]

        with DBConnection() as db:
            curs = db.cursor
            curs.execute(
                f"SELECT miles, dateLastODO, milesPerDay FROM vehicles WHERE vehicleID = {id}")
            newODO, newDateLast, newMilesPerDay = curs.fetchall()[0]
            newODO = float(newODO)

        # In updateODO we want to detect if odo is None. We need to make a sepcial case
        # and take a sepcial default action that doesn't blow up the mileage estimates.
        # In that case, let miles per day be 0 to prevent unneccesary service reminders
        # until there is a regular cadence of updates.
        # dailyMaint will check if there is no previous odo reading as well.
        if not oldODO or not oldDateLast or not oldMilesPerDay:
            testMilesPerDay = 0  # updateODO should be setting the rate to 0
        else:
            daydiff = (sampleToday-oldDateLast).days
            if daydiff == 0:
                testMilesPerDay = oldMilesPerDay
            else:
                testMilesPerDay = \
                    (newODO - float(oldODO)) / daydiff

        assert newODO == round(testODO, 1)
        assert newDateLast == sampleToday
        assert round(newMilesPerDay, 1) == round(testMilesPerDay, 1)

    runTest(1, 110000.1)
    runTest(2, 1030001)
    runTest(3, 1000000.93)
    runTest(4, 1001)
    runTest(5, 200000.19001)
    runTest(6, 300000)

    with raises(main.NotInDatabaseError):
        runTest(id=0, testODO=0)

    with raises(ValueError):
        runTest(id=1, testODO=1)


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
                main.updateServiceDone(itemID=id, itemODO=odo)
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


# Just compare that the function can find all the flagged service items
def test_notifyAllService():
    buildSampleDB()

    query = """SELECT itemID FROM serviceSchedule
        WHERE servDueFlag = TRUE"""

    with DBConnection() as db:
        cur = db.cursor
        cur.execute(operation=query)
        res = cur.fetchall()

    assert res == main.notifyAllService()


# dailyMaint:
# run dailyMaint and gather some data using mock functions:
# mock the output of getDateToday to be some set value, this will produce a consistent test.
# check that the right list of users has been prompted (don't actually prompt anyone)
# check that the vehicles table has been updated correctly.
# check that the service schedule has been updated correclty.
def test_dailyMaint(mocker):
    # a list of users that will be populated by dailyMaint when it calls the (mocked) promptUser function
    promptedUsersIntrospect = []

    def promptUserSideEffect(usr):
        promptedUsersIntrospect.append(usr)
        return ("not_a_phone", "not_a_message")

    def runTest(simulatedTodayDate):
        main.dailyMaint()

        # check that the right list of users has been prompted
        # get a list from the db of the users that should be called.
        with DBConnection() as db:
            c = db.cursor
            c.execute(f"""
                SELECT DISTINCT userID FROM vehicles
                WHERE DATEDIFF('{testDate}', dateLastODO) > '{main.ODOPROMPTINTERVAL}'
            """)
            result = c.fetchall()

            refUsersList = []
            for item in result:
                refUsersList.append(item[0])

            assert refUsersList == promptedUsersIntrospect

            # check that the vehicles table has been updated correctly.
            # TEST: estMiles should be updated to miles + milesperday * days elapsed when miles not null.
            # TEST: IF Miles is NULL. then estMiles and milesPerDay should be set to 0.
            # TEST: milesPerDay should not be null anywhere. do this as a separate test.

            c.execute("""
                SELECT estMiles = (miles + milesPerDay * DATEDIFF('2025-09-15', dateLastODO))
                FROM vehicles
                WHERE miles IS NOT NULL
            """)
            result1 = c.fetchall()
            for assertion in result1:
                if assertion[0] != 1:
                    breakpoint()
                assert assertion[0]

            c.execute("""
                SELECT estMiles = 0 AND milesPerDay = 0
                FROM vehicles
                WHERE miles IS NULL
            """)
            result2 = c.fetchall()
            for assertion in result2:
                if assertion[0] != 1:
                    breakpoint()
                assert assertion[0]

            c.execute("""
                SELECT milesPerDay IS NOT NULL
                FROM vehicles
            """)
            result3 = c.fetchall()
            for assertion in result3:
                if assertion[0] != 1:
                    breakpoint()
                assert assertion[0]

    # mock the today's date function.
    testDate = date(2025, 9, 15)
    mocker.patch('main.getDateToday', return_value=testDate)

    # mock the promptUserForoneveh function such that we can read out params for all calls to it.
    mockPromptUser = mocker.patch('main.promptUserForOneVeh')
    mockPromptUser.side_effect = promptUserSideEffect
    mockPromptUser.return_value = ("not_a_phone", "not_a_message")

    # mock sendSMS to avoid calls to it. doesn't need to do anything
    mocker.patch('main.sendSMS')

    buildSampleDB()

    # run the test 10 times to simulate 10 days of maintenance.
    for days in range(0, 9):
        runTest(testDate + timedelta(days=days))
        # reset for the next test.
        promptedUsersIntrospect = []


def test_homepage(client):
    response = client.get("/")
    assert response.status_code == 200


# def test_receiveOdoMsg
# utilize the client and mocker features to simulate a post to the route
# and then mock the call to updateODO to check the function's work.
# the mocked updateODO should also simulate some exceptions which then
# need to be handled
# Also check the return values of receiveOdoMsg
def test_receiveOdoMsg(client, mocker):
    # mock things:
    # -
    # def a runTest function
    def runTest(httpRequest=""):
        # in pytest, there is no with does not raise thing.
        # But, and this gets into python "contexts",
        # "does not raise" is basically equivalent to a nothing context.
        # so for the sake of better readable code I'm implementing
        # "nullcontext" as "does_not_raise"
        # again, if this function throws an exception it is a problem outside of the
        # scope I am willing to take for this project. So must avoid throwing an exception.
        with does_not_raise:
            resp = client.post("SOMETHING")

        # is the response an instance of MessagingResponse?
        assert isinstance(resp, MessagingResponse)

        # get the response message back and parse it for relevant data.
        respMsg = resp.message

        # get the httpRequest and parse it to form the logical statements that cause
        # assertions.
        phone = "1234134"  # get the phone number from the request
        smsContent = "Somecontent"  # get the message content.

        # -input handling:
        #  'From' should just be a phone number. no need to test.
        #  -Handle the case in which the vehicleID is not in the database. This should throw
        #    an exception which should be handled, causing receiveOdoMsg to return a corresponding message.
        #      -Handle bad odo readings:
        #          -odo is not a number
        #          -odo is a number, but is:
        #              -lower than the vehicle's odo and therefore invalid.
        #              -negative
        #              -too large. since DB sets a maximum amount in the schema, investigate using an excetion thrown by MySQL
        #                  for this.
        # check are we testing for these conditions? Do the params cause these violations?
        testNoMatchingVehInDB = True
        testOdoIsNotNumber = True
        odoLowerThanVehMiles = True
        odoIsNeg = True
        odoTooLarge = True

        if testNoMatchingVehInDB:
            assert True

        if testOdoIsNotNumber:
            assert True

        if odoLowerThanVehMiles:
            assert True

        if odoIsNeg:
            assert True

        if odoTooLarge:
            assert True

        #   -calls updateODO using the correct inputs.
        #       -use some sort of list variable updated by a side effect of the mocked updateODO
        #       -mocked updateODO should also raise exceptions for things that concern updateODO.
        return
    # assert the outputs
    # call runTest for exception cases specifically, however this may not look like
    #    raising an exception but moreso returning a message that indicates an error.
    # generate fake calls and call runTest
    return


# testDate = date(2025, 9, 15)
# for days in range(0, 9):
#     print(testDate + timedelta(days=days))
