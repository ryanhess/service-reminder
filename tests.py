import main
import DB_Builder
from DB_Builder import DBConnection
from mysql.connector import connect, Error
from pytest import fixture, raises
from decimal import Decimal
import pytest_mock
from datetime import date, timedelta
from twilio.twiml.messaging_response import MessagingResponse
from contextlib import contextmanager, nullcontext as does_not_raise
# import flask
from flask import Response, url_for
import xml.etree.ElementTree as ET  # for parsing responses from routes.


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


def getSampleTodayStr():
    return getSampleToday().strftime('%Y-%m-%d')


def getSampleToday():
    return date(2025, 9, 15)


# get Max Value for Column in table
# returns the theoretical maximum value for a given column in a given table schema
# if the data type is a decimal. (flesh this out later to more data types
# if it serves a purpose.)
def getMaxTheoValueDecimal(tableName="", columnName=""):
    result = main.querySQL(f"""
        SELECT numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = "service_reminders_app"
        AND table_name = "{tableName}"
        AND column_name = "{columnName}"
        AND data_type = "decimal"
    """)
    if result == []:
        return "Column is not Decimal type"
    else:
        digitsLeftDecimal = result[0][0] - 1
        digitsRightDecimal = result[0][1]
        return 10 ** digitsLeftDecimal - 10 ** (-1 * digitsRightDecimal)


### TESTS ###


def test_getDateToday():
    assert main.getDateTodayStr() == date.today().strftime('%Y-%m-%d')


# for a user, get the vehicle that is eligible for an update.
# if vehicle is more than x days out of date
# and its the most out of date vehicle.
# if the dateLastODO or miles is none, that vehicle jumps to the top
# return none if there is no eligible vehicle.
def test_getUserUpdateVehicle(mocker):
    mocker.patch('main.getDateToday', return_value=getSampleToday())
    buildSampleDB()

    users = main.querySQL("""
        SELECT userID FROM users
    """)

    # based on the sample database.
    anticipatedResults = [None, 3, 4, 6, 8, 9]

    for (user, result) in zip(users, anticipatedResults):
        funcResult = main.getUserUpdateVehicle(user[0])
        print(funcResult)
        assert result == funcResult

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
    #   -dateLastODO is None. In that case, it is the most out of date.


# no longer need to test that the right vehicle is retrieved, just
# that the right messages are generated. Once again mock today
def test_promptUserForOneVeh(mocker):
    mocker.patch('main.getDateToday', return_value=getSampleToday())
    # today = date(2025, 9, 15)
    buildSampleDB()  # rebuild the database with some sample data.
    # test user 4. should return data "hey Soraya," "Grandma" stuff stuff.
    phone, msg = main.promptUserForOneVeh(usrID=4)
    assert phone == "+19178487133"
    assert "sorayah" in msg and "Grandma" in msg

    # test user 3. should return subaru outback because it has None for dateLastODO
    phone, msg = main.promptUserForOneVeh(usrID=3)
    assert phone == "+19177978174"
    assert "brianhess" in msg and "2025 Subaru Outback" in msg

    # test user 1000. should raise a custom exception
    with raises(main.NotInDatabaseError):
        main.promptUserForOneVeh(usrID=1000)

    # test user 0. same result.
    with raises(main.NotInDatabaseError):
        main.promptUserForOneVeh(usrID=0)

    # test user 1. should return one or the other of "detectivemiller" and "millertruck1" or "millertruck2"
    phone, msg = main.promptUserForOneVeh(usrID=7)
    assert phone == "+12345678901"
    assert "detectivemiller" in msg and \
        (("millertruck1" in msg) != ("millertruck2" in msg))


# needs to test that the function performs the expected result which is:
#   vehicleID's ODO value is updated to the input value
#   updates the dateLastODO
#   updates the milesPerDay to the correct value.
# raises NotInDatabaseError when vehicle is not in the database
# raises ValueError if the inputted miles are less than the ODO value on record.
# raises TypeError if the parameter cannot be typed to Float
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
    runTest(6, 300000)
    runTest(11, 10)

    with raises(main.NotInDatabaseError):
        runTest(id=0, testODO=0)

    # if the passed odo is less than the one stored for veh, function should
    # raise a ValueError
    with raises(ValueError):
        runTest(id=1, testODO=1)

    with raises(ValueError):
        runTest(5, 200000.19001)

    with raises(TypeError):
        runTest(13, 'haha not a number')


# check that the service-due-flag is now false.
# check that the mileage deadline is now extended by the mileage interval plus the odo value
# check that when odo is less than parent miles, the parent miles is not updated.
# check proper NotInDatabaseError.
# runTest should return True if parent miles (after db operations) equals the odo passed.
# in oher words, the DB integrity is preserved and the odo values is rejected.
def test_updateServiceDone():
    def runTest(id, odo):
        # Pass any raised exceptions out to the caller.
        try:
            main.updateServiceDone(itemID=id, itemODO=odo)
        except:
            raise
        
        res = main.querySQL(f'''
            SELECT vehicleID, serviceInterval, dueAtMiles, servDueFlag
            FROM serviceSchedule
            WHERE itemID = {id}
        ''')

        veh, interval, dueAt, flag = res[0]

        res = main.querySQL(f'''
            SELECT miles FROM vehicles
            WHERE vehicleID = {veh}''')
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
                INSERT INTO serviceSchedule (vehicleID, userID, description, serviceInterval, milesLastDone, servDueFlag)
                VALUES ( %s, %s, %s, %s, %s, %s )
            """

            sampleServiceSched = [
                (1, 1, "Change Eng. Oil and Filter", 5000, 6030, True),
                (1, 1, "Rotate and Inspect Tires", 5000, 95300, True),
                (1, 1, "Re-torque drive shaft bolts", 15000, 105000, True),
                (2, 1, "Change Eng. Oil and Filter", 5000, 125000, True),
                (2, 1, "Replace Brake Fluid", 10000, 116000, True),
                (3, 2, "Change tires", 1, 0, False),
                (4, 3, "change oil", 1, 5999, False),
                (5, 4, "flush brakes", 2, 98, True),
                (6, 4, "set alignmnet", 10, 1028990, False)
            ]

            cur.executemany(sampleServSchedStmt, sampleServiceSched)

    buildBlankDB()
    populateDB()

    # check for not in database
    with raises(main.NotInDatabaseError):
        runTest(0, 0)
    with raises(main.NotInDatabaseError):
        runTest(9999, 0)

    # check for passing not a number that can be cast to a float.
    with raises(TypeError):
        runTest(1, 'asdf')
    
    # check for values out of range
    with raises(ValueError):
        runTest(1, '100000000')
        runTest(1, 100000000)

    with raises(ValueError):
        runTest(1, -100)
        runTest(1, '-100')

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

            c.execute(f"""
                SELECT estMiles = (miles + milesPerDay * DATEDIFF('{testDate}', dateLastODO))
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


# def test_receiveOdoMsg
# utilize the client and mocker features to simulate a post to the route
# and then mock the call to updateODO to check the function's work.
# the mocked updateODO should also simulate some exceptions which then
# need to be handled
# Also check the return values of receiveOdoMsg
# figuring out something about testing herE:
# if I make all the testing automated I start to have trouble debugging the test code on top of the code dode.
# instead of checking the inputs in the test code an then comparing the tests of the inputs in the real code,
# just check the real code against a hard-coded "expected result" that I can easily read off in the test_...() function,
# using print statements.
# then if there is a failure I first can check my function calls that I am asserting the right outputs.
def test_receiveOdoMsg(client, mocker):
    updatedVehIntrospect = None

    def updateOdoMockFunc(vehID, newODO):
        # if updateOdo would return an error this will remain
        # None
        nonlocal updatedVehIntrospect
        updatedVehIntrospect = None

        result = main.querySQL(f"""
            SELECT miles FROM vehicles
            WHERE vehicleID = {vehID}
        """)

        if result == []:
            raise main.NotInDatabaseError("MOCK: veh not in DB")

        vehODO = result[0][0]
        if vehODO is not None:
            if float(vehODO) > float(newODO):
                raise ValueError("MOCK: new ODO less than vehicle odo.")

        # since the function would work, set the variable to the vehID
        updatedVehIntrospect = vehID

    todaySample = date(2025, 9, 15)

    # mock things:
    mockUpdateODO = mocker.patch('main.updateODO')
    mockUpdateODO.side_effect = updateOdoMockFunc
    mocker.patch('main.getDateToday', return_value=todaySample)

    def runTest(fromPhone, smsBody):
        print(
            f"\nreceiveOdoMsg From '{fromPhone}' Message reads: '{smsBody}'. Expected message: ")
        # set up our fake http POST request.
        # no need for setting the content type since flask sets this when
        # you set the data param of .post()
        # therefore, there is no need to include headers since
        # since I am not checking any in this version of receiveOdoMsg
        with main.app.test_request_context():
            route = url_for('receiveOdoMsg')   
        data = {
            'From': fromPhone,
            'Body': smsBody
        }

        # use null_context as does_not_raise to indicate that we assert
        # this won't raise an exception.
        with does_not_raise():
            response = client.post(path=route, data=data)

        # # does receiveOdoMsg return a status code 200?
        assert response.status_code == 200

        # response will be a Flask.Response object. From this we must unpack
        # twiML that encodes the respnse. This is xml.
        respData = response.get_data()

        # will it parse as XML? Then it is PROBABLY twiML
        try:
            twiMLReturnMessage = ET.fromstring(respData)
        except ET.ParseError:
            assert False

        # is the root a 'response' tag with a 'message' tag nested in? Then it is
        # twiml enough for me!
        assert twiMLReturnMessage.tag == 'Response'
        msgElem = twiMLReturnMessage.find('Message')
        assert msgElem is not None

        responseStr = msgElem.text
        return responseStr
        
        maxODO = getMaxTheoValueDecimal('vehicles', 'miles')
    
        # # these checks should cascade because these conditions shouldnt overlap
        # # (ie no user in db and no matching veh)
        # # these if/else statements basically paraphrase the message so I can easily hardcode
        # # in a more readable way outside. Then a paraphrased message is returned.
        # if main.PHONENOTINDBSMS in responseStr:
        #     print(main.PHONENOTINDBSMS)
        #     return main.PHONENOTINDBSMS
        # elif main.NOELIGIBLEVEHICLESMS in responseStr:
        #     print(main.NOELIGIBLEVEHICLESMS)
        #     return main.NOELIGIBLEVEHICLESMS
        # elif main.NOTANUMBER.format(what='response') in responseStr:
        #     print(main.NOTANUMBER.format(what='response'))
        #     return main.NOTANUMBER.format(what='response')
        # elif "can't be negative." in responseStr:
        #     print("negative")
        #     return "negative"
        # elif f"number can't be more than {maxODO}" in responseStr:
        #     print("too large")
        #     return "too large"
        # elif "must be more than your vehicle's last recorded miles." in responseStr:
        #     print("less than recorded miles")
        #     return "less than recorded miles"
        # elif "Successfully updated the odometer" in responseStr:
        #     print("no input errors")
        #     return "no input errors"
        # else:
        #     print("UNCAUGHT INPUT ERROR")
        #     return "UNCAUGHT INPUT ERROR"

    # bad user inputs
    assert main.PHONENOTINDBSMS in runTest(
        fromPhone='+114142; drop table users', smsBody='adsfasdf')

    assert main.NOELIGIBLEVEHICLESMS in runTest(
        fromPhone='+18777804236', smsBody='1234')
    assert main.ODONOTANUMBER in runTest(
        fromPhone='+16469576453', smsBody='asdf')
    assert main.ODOBELOWZERO in runTest(
        fromPhone='+16469576453', smsBody='-0110')
    assert main.ABOVEMAX.format(max='') in runTest(
        fromPhone='+16469576453', smsBody='11923481932489132498')
    assert main.ODODECREASING in runTest(
        fromPhone='+16469576453', smsBody='1')
    assert main.ODONOTANUMBER in runTest(
        fromPhone='+19178487133', smsBody='hello')
    assert main.ODONOTANUMBER in runTest(
        fromPhone='+19178487133', smsBody='123; drop table users')
    assert main.ODOBELOWZERO in runTest(
        fromPhone='+19178487133', smsBody='-100')
    assert main.ABOVEMAX.format(max='') in runTest(
        fromPhone='+19178487133', smsBody='1293128938931289')
    assert main.ODODECREASING in runTest(
        fromPhone='+19178487133', smsBody='9')

    # good inputs "today" is 9/15/2025
    assert main.SUCCESSFULODOUPDATESMS in runTest(
        fromPhone="+18006969008", smsBody="300.25")
    assert main.SUCCESSFULODOUPDATESMS in runTest(
        fromPhone="+17974087089", smsBody="2.6")
    assert main.SUCCESSFULODOUPDATESMS in runTest(
        fromPhone="+19177978174", smsBody="5")
    assert main.SUCCESSFULODOUPDATESMS in runTest(
        fromPhone="+100", smsBody="6")


# test all GET web routes
def test_webUserRoutes(client):
    response = client.get('/')
    assert response.status_code == 200

    response = client.get('/Users')
    assert response.status_code == 200

    response = client.get('/Users/New')
    assert response.status_code == 200

    response = client.get('/Users/1')
    assert response.status_code == 200

    response = client.get('/Users/blah')
    assert response.status_code == 404

    response = client.get('/Users/100000000000')
    assert response.status_code == 404

    response = client.get('/Users/1/New-Vehicle')
    assert response.status_code == 200

    response = client.get('/Vehicles/1')
    assert response.status_code == 200

    response = client.get('/Vehicles/0')
    assert response.status_code == 404

    response = client.get('/Vehicles/blah')
    assert response.status_code == 404

    response = client.get('/Vehicles/1/New-Service')
    assert response.status_code == 200
    
    response = client.get('/Vehicles/0/New-Service')
    assert response.status_code == 404

    response = client.get('/Vehicles/blah/New-Service')
    assert response.status_code == 404

    response = client.get('/Vehicles/4/Update-Odometer')
    assert response.status_code == 200

    response = client.get('/Vehicles/1/Update-Odometer')
    assert response.status_code == 200

    response = client.get('/Vehicles/0/Update-Odometer')
    assert response.status_code == 404

    response = client.get('/Vehicles/blah/Update-Odometer')
    assert response.status_code == 404

    response = client.get('/Service/4/Update-Service-Done')
    assert response.status_code == 200

    response = client.get('/Service/1/Update-Service-Done')
    assert response.status_code == 200

    response = client.get('/Service/0/Update-Service-Done')
    assert response.status_code == 404

    response = client.get('/Service/blah/Update-Service-Done')
    assert response.status_code == 404

# simple createUser function
# check that the user is created.
# testing input filtering is coming soon.
# test that the new user is in the database and that status code is 200
def test_newUserUIPOST(client, mocker):
    spiedUser = spiedPhone = spiedErrMsg = None

    def mock_render_template(unusedTemplateFile="", userInfo={'username': None, 'phone': None}, error=False, errorMessage=""):
        nonlocal spiedUser, spiedPhone, spiedErrMsg
        spiedUser = userInfo['username']
        spiedPhone = userInfo['phone']
        spiedErrMsg = errorMessage

        return Response(status=200)

    renderMock = mocker.patch('main.render_template')
    renderMock.side_effect = mock_render_template

    # checks that username is in db and that its phone is 'phone'
    # if either is not true, return false.
    # should be passed sanitized and properly formatted data or
    # it will return false.
    def checkUserInDB(usrN, phone):
        res = main.querySQL(stmt=f'''
            SELECT userID From users
            WHERE username = '{usrN}'
        ''')

        if res == []:
            return False
        else:
            userID = res[0][0]
            res = main.querySQL(stmt=f'''
                SELECT phone FROM users
                WHERE userID = '{userID}'
            ''')
            return res[0][0] == str(phone)

    # runs some tests inside and returns the error message that would be
    # displayed so inputs can be asserted outside.
    # if no error, returns 0 so that "assert not runTest()" is asserting that
    # the function runs with no errors.
    def runTest(usr, phone):
        nonlocal spiedUser, spiedPhone, spiedErrMsg
        with main.app.test_request_context():
            route = url_for('newUserUI')   
        form = {
            'username': usr,
            'phone': phone
        }

        response = client.post(path=route, data=form)

        # no matter what the server should respond with a webpage.
        # in this case, I am artificially generating responses
        # but if it gets to render_template without errors I think that
        # is enough for the test function to test.
        assert response.status_code == 200

        if spiedErrMsg:
            msg = spiedErrMsg
        else:
            assert checkUserInDB(spiedUser, spiedPhone)
            msg = 0

        spiedUser = spiedPhone = spiedErrMsg = None

        return msg

    # asserting a username that already exists
    assert main.ILLEGALDUPLICATE.format(param='username') in runTest(
        usr='ryanhess', phone='1414144444')

    # asserting a phone number that already exists
    assert main.ILLEGALDUPLICATE.format(param='phone') in runTest(
        usr='thepinkpanther', phone='+18777804236'
    )

    # asserting correct operation with good inputs
    assert not runTest(usr='newUserTest123', phone='+12838812931')
    assert not runTest(usr='###fsf23', phone='+14838812931')


# empty
def test_newVehicleUIPOST(client, mocker):
    spiedVehID = spiedDispName = spiedMiles = spiedErrMsg = None

    def mock_render_template(unusedTemplateFile="",
            user={'id': None, 'username': None},
            vehicle={'id': None, 'displayName': None, 'miles': None},
            errorMessage=""):
        nonlocal spiedVehID, spiedDispName, spiedMiles, spiedErrMsg
        spiedVehID = vehicle['id']
        spiedDispName = vehicle['displayName']
        spiedMiles = vehicle['miles']
        spiedErrMsg = errorMessage

        return Response(status=200)

    renderMock = mocker.patch('main.render_template')
    renderMock.side_effect = mock_render_template

    # check that the vehicle ID is NOT in a set of old ids
    # (it is original)
    # and that it is NOW in the database.
    # use a context manager to control the state of this. 
    def checkVehCreated(vehID, oldIDs):
        try:
            vehID = int(vehID)
        except ValueError:
            return False

        if vehID not in oldIDs:
            res = main.querySQL(stmt='''
                SELECT vehicleID From vehicles
                WHERE vehicleID = %s
            ''', val=(vehID,))

            if res == []:
                return False
            else: 
                return True
        else:
            return False

    # runs some tests inside and returns the error message that would be
    # displayed so inputs can be asserted outside.
    # if no error, returns 0 so that "assert not runTest()" is asserting that
    # the function runs with no errors.
    def runTest(userID, vehicle):
        nonlocal spiedVehID, spiedDispName, spiedMiles, spiedErrMsg
        with main.app.test_request_context():
            route = url_for('newVehicleUI', userID=userID)   

        beforeVehIDs = set()
        res = main.querySQL(stmt='''
            SELECT vehicleID FROM vehicles
        ''')
        for result in res:
            beforeVehIDs.add(result[0])

        response = client.post(path=route, data=vehicle)

        # we need to be able to detect that specifically:
        # the function fails to return a 200 code. This is
        # by changing the returned message to indicate the
        # response code if not 200.
        if response.status_code != 200:
            return response.status_code

        if spiedErrMsg:
            msg = spiedErrMsg
        else:
            nick = vehicle['nickname']
            calcDispName = nick if nick and len(nick) > 0 \
                else vehicle['year'] + ' ' + vehicle['make'] + \
                ' ' + vehicle['model']
            assert calcDispName == spiedDispName
            assert vehicle['miles'] == spiedMiles
            assert checkVehCreated(vehID=spiedVehID, oldIDs=beforeVehIDs)
            msg = 0

        spiedDispName = spiedMiles = spiedErrMsg = None

        return msg
    
    # sql injection should be harmlessly treated as any other string
    assert 0 == runTest(userID=2, vehicle={'nickname': 'hello; drop table users;',
                                            'year': '2000',
                                            'make': 'lex',
                                            'model': 'blah; drop table users;',
                                            'miles': ''})
    
    # incomplete form requests should return a 400.
    # also, in Flask a None value is equivalent to a missing key.
    assert 400 == runTest(userID=1, vehicle={})
    assert 400 == runTest(userID=3, vehicle={'nickname': 'hello'})
    assert 400 == runTest(userID=3, vehicle={'nickname': 'hello',                           
                                            'year': '2000'})
    assert 400 == runTest(userID=3, vehicle={'nickname': 'hello',                           
                                            'year': '2000',
                                            'make': 'lex'})
    assert 400 == runTest(userID=3, vehicle={'nickname': 'hello',                           
                                            'year': '2000',
                                            'make': 'lex',
                                            'model': 'blah'})
    assert 400 == runTest(userID=3, vehicle={'nickname': 'hello',                           
                                            'year': '2000',
                                            'make': 'lex',
                                            'model': 'blah',
                                            'miles': None})

    # assert input errors.
    assert main.FORMFIELDBLANK.format(field='year') in \
        runTest(userID=3, vehicle={'nickname': 'hello',                           
                                    'year': '',
                                    'make': 'lex',
                                    'model': 'blah',
                                    'miles': 20})
    assert main.INVALIDPARAM.format(param='year') in \
        runTest(userID=1, vehicle={'nickname': 'hello',                           
                                    'year': '-5',
                                    'make': 'lex',
                                    'model': 'blah',
                                    'miles': ''})
    assert main.INVALIDPARAM.format(param='year') in \
        runTest(userID=1, vehicle={'nickname': 'hello',                           
                                    'year': 'not a year',
                                    'make': 'lex',
                                    'model': 'blah',
                                    'miles': ''})    
    # having no nickname in the request should be OK
    assert not runTest(userID=3, vehicle={'nickname': '',                           
                                    'year': '2000',
                                    'make': 'lex',
                                    'model': 'blah',
                                    'miles': ''})
    assert main.FORMFIELDBLANK.format(field='make') in \
        runTest(userID=1, vehicle={'nickname': 'hello',                           
                                    'year': '2000',
                                    'make': '',
                                    'model': 'blah',
                                    'miles': ''})
    
    assert main.FORMFIELDBLANK.format(field='model') in \
        runTest(userID=1, vehicle={'nickname': 'hello',                           
                                    'year': '2000',
                                    'make': 'make',
                                    'model': '',
                                    'miles': ''})
    assert main.NOTANUMBER.format(what='miles') in \
        runTest(userID=1, vehicle={'nickname': 'hello',                           
                                    'year': '2000',
                                    'make': 'make',
                                    'model': 'blah',
                                    'miles': 'word'})
    assert main.ODOBELOWZERO in \
        runTest(userID=1, vehicle={'nickname': 'hello',                           
                                    'year': '2000',
                                    'make': 'make',
                                    'model': 'blah',
                                    'miles': '-1'})
    
    #should be good inputs
    assert not runTest(userID=1, vehicle={'nickname': '',                           
                                    'year': '2000',
                                    'make': 'make',
                                    'model': 'blah',
                                    'miles': ''})
    assert not runTest(userID=1, vehicle={'nickname': 'nickname',                           
                                    'year': '2000',
                                    'make': 'make',
                                    'model': 'blah',
                                    'miles': '1000'})
    

# empty
def test_UpdateODOUIPOST(client, mocker):
    return


# empty
def test_newServiceUIPOST(client, mocker):
    return


# empty
def test_UpdateServiceDoneUIPOST(client, mocker):
    return