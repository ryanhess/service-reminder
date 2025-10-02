import os
from decimal import *
from datetime import date
# from urllib.parse import parse_qs
from mysql.connector import connect, Error
from flask import Flask, request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

ODOPROMPTINTERVAL = 7  # the number of days to wait before prompting a regular ODO reading


app = Flask(__name__)

# function to get today's date in YYYY-mm-dd


def getDateToday():
    return date.today()


def getDateTodayStr():
    return getDateToday().strftime('%Y-%m-%d')


def strIsFloat(str=""):
    try:
        float(str)
    except ValueError:
        return False
    else:
        return True


# get Max Value for Column in table
# returns the theoretical maximum value for a given column in a given table schema
# if the data type is a decimal. (flesh this out later to more data types
# if it serves a purpose.)
def getMaxTheoValueDecimal(tableName="", columnName=""):
    result = querySQL(f"""
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

### custom exceptions ###


# exception thrown when a given row is not found in DB
class NotInDatabaseError(Exception):
    pass


# function to execute SQL query in a safe container, opening and closing the connection and checking for errors along the way.
# returns the result of a query if there is one.
def querySQL(stmt="", val="", many=False):
    try:
        with connect(
            host="localhost",
            user="serv-rem-dev",
            password="password",
            database="service_reminders_app"
        ) as connection:
            c1 = connection.cursor()

            if many:
                c1.executemany(stmt, val)
            else:
                c1.execute(stmt, val)
            result = c1.fetchall()
            connection.commit()

            return result

    except Error as e:
        raise Exception(e)


def sendSMS(recip="", msg=""):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    client = Client(account_sid, auth_token)

    message = client.messages.create(
        body=msg,
        from_="+18665934611",
        to=recip,
    )


# get eligible vehicle for the user.
def getUserUpdateVehicle(userID):
    result = querySQL(f'''
        SELECT vehicleID
        FROM vehicles
        WHERE userID = {userID}
        AND (dateLastODO IS NULL OR miles IS NULL)
        LIMIT 1
    ''')

    if result != []:
        return result[0][0]
    else:
        result = querySQL(f'''
            SELECT vehicleID FROM vehicles
            WHERE userID = {userID}
            AND DATEDIFF('{getDateToday()}', dateLastODO) > {ODOPROMPTINTERVAL}
            ORDER BY dateLastODO ASC
            LIMIT 1
        ''')
        if result == []:
            return None
        else:
            return result[0][0]


def promptUserForOneVeh(usrID=0):
    vehID = getUserUpdateVehicle(usrID)
    if vehID is None:
        raise NotInDatabaseError("no eligible vehicle found for user.")

    queryResult = querySQL(stmt=f'''
            SELECT vehNickname, make, model, year 
            FROM vehicles 
            WHERE vehicleID = {vehID}
    ''')

    nick, make, modl, year = queryResult[0]

    # we need the user name and the phone number from the user.
    queryResult = querySQL(stmt=f'''
        SELECT username, phone FROM users
        WHERE userID = {usrID}
    ''')
    (username, phone) = queryResult[0]

    msg = f"""Hey {username}, Service Reminders here. Please reply with an odometer reading for {
        nick if nick != None else ' your ' + str(year) + ' ' + make + ' ' + modl}."""

    return phone, msg


# def:
# update a vehicle odometer in database with the given odo
# this should check that the new ODO reading is greater than the previous ODO reading. Should reply to the user confirming the reading or prompting again if the reading contains an error.
# Calculate and store a new average miles per day given the prev ODO reading and the days since the last ODO reading.
def updateODO(vehID=0, newODO=0):
    today = getDateToday()

    if newODO is None:
        newODO = 0

    res = querySQL(f"""
        SELECT miles, dateLastODO, milesPerDay FROM vehicles WHERE vehicleID = {vehID}
    """)

    if res == []:
        raise NotInDatabaseError(
            "vehicle with this ID does not exist in the database.")

    curMiles, curOdoDate, curMilesPerDay = res[0]

    # In updateODO we want to detect if odo is None. We need to make a sepcial case.
    # and take a sepcial default action that doesn't blow up the mileage estimates.
    # In that case, let miles per day be 0 to prevent unneccesary service reminders
    # until there is a regular cadence of updates.
    # dailyMaint will check if there is no previous odo reading as well.
    # checking for null values in the other values is kind of "extra" and really
    # there just to keep things moving. I don't expect cases where these values
    # will be None in a production setting.
    if not curMiles or not curOdoDate or not curMilesPerDay:
        curMiles = 0
        curOdoDate = today
        curMilesPerDay = 0

    elif curMiles > newODO:
        raise ValueError(
            "cannot update the mileage with a lesser number than the current value")

    # we have to account for if the odo is updated again on the same day.
    try:
        newMilesPerDay = (newODO - float(curMiles)) / (today - curOdoDate).days
    except ZeroDivisionError:
        newMilesPerDay = curMilesPerDay

    querySQL(f"""
        UPDATE vehicles
        SET miles = {round(newODO, 1)}, dateLastODO = '{today}', milesPerDay = {newMilesPerDay}
        WHERE vehicleID = {vehID}
    """)


# def:
# update the records indicating a service was done at a given miles
# should remove the service due flag, update the mileage deadline, and update the ODO for the vehicle only if this ODO is greater than the ODO stored for the vehicle.
def updateServiceDone(itemID=0, itemODO=0):
    if not itemODO:  # if servODO is None
        itemODO = 0

    # check for not the right type
    # update the miles of the parent vehicle, only if the new ODO is greater than the previous ODO.
    res = querySQL(f"""
        SELECT vehicleID, miles FROM vehicles
        WHERE vehicles.vehicleID = (SELECT vehicleID FROM serviceSchedule WHERE serviceSchedule.itemID = {itemID})
    """)
    if res == []:
        raise NotInDatabaseError(
            f"serviceSchedule record for itemID = {itemID} does not exist.")

    # yes, I know updateODO checks for this and throws an exception,
    # but this is not an error. Dont want to trip an exception.
    vehID, parentMiles = res[0]
    if not parentMiles or itemODO > parentMiles:
        # update the miles of the parent vehicle.
        updateODO(vehID, itemODO)

    # remove the service flag and update the dueAtMiles.
    querySQL(f"""
        UPDATE serviceSchedule
        SET dueAtMiles = {itemODO} + serviceInterval, servDueFlag = FALSE
        WHERE itemID = {itemID}
    """)


# def:
# check the database for service that is due and notify the relevant user. The caller of this function sets the frequency of the reminders.
def notifyOneService(serviceItemID):
    res = querySQL(stmt=f"""
        SELECT userID, vehicleID, description, dueAtMiles FROM serviceSchedule
        WHERE itemID = {serviceItemID}
    """)
    if res == []:
        raise NotInDatabaseError(
            f"Service item {serviceItemID} was not found in the database.")
    usrID, vehID, desc, dueAt = res[0]

    res = querySQL(stmt=f"""
        SELECT username, phone FROM users
        WHERE userID = {usrID}
    """)
    username, phone = res[0]

    res = querySQL(stmt=f"""
        SELECT vehNickname, year, make, model FROM vehicles
        WHERE vehicleID = {vehID}
    """)

    nick, year, make, model = res[0]

    msg = f"""
        {username}, {nick if nick != None else ' your ' + str(year) + ' ' + str(make) + ' ' + str(model)}
        is due for item: "{desc}" at {dueAt} miles.
    """

    return phone, msg


# def:
# check the DB for service that is due and call notifyOneService for each item due.
# Send the returned message to the returned phone number by calling sendSMS
def notifyAllService():
    query = """
        SELECT itemID FROM serviceSchedule
        WHERE servDueFlag = TRUE
    """
    flaggedItems = querySQL(stmt=query)

    # get the ymm and nick of the vehicle in the item
    # get the username and phone number of the user
    # {username}, your {ymm}/{nick} is due for {item} at {x} miles.
    for item in flaggedItems:
        phone, msg = notifyOneService(item[0])

        # send the message.
        sendSMS(recip=phone, msg=msg)

    return flaggedItems


# def:
# should be called at least every day.
# check on the vehicle database, update values, and call for sending messages to the user. This should happen at a regular interval determined by the caller.
def dailyMaint():
    # get a list of userIDs which are from vehicles which have out of date ODO readings.
    query = f"""
        SELECT DISTINCT userID FROM vehicles
        WHERE DATEDIFF('{getDateTodayStr()}', dateLastODO) > '{ODOPROMPTINTERVAL}'
    """
    queryResult = querySQL(stmt=query)
    # sort the list by userID, then by dateLastODO oldest to newest. This ensures that the highest priority is to query the most out of date vehicle.
    for usr in queryResult:
        phone, msg = promptUserForOneVeh(usr[0])
        sendSMS(recip=phone, msg=msg)

    # calculate a new mileage estimate for all vehicles.
    # deal with the case in which miles is NULL.
    # if miles is NULL, estMiles and milesPerDay should be set to 0.
    # For code robustness, but not really a high-demand case, do the same
    # when milesPerDay is NULL as well we are setting estMiles so no need for that.

    # THE ORDER OF THESE QUERIES IS IMPORTANT
    queryForNull = f"""
        UPDATE vehicles
        SET estMiles = 0,
            milesPerDay = 0
        WHERE miles IS NULL OR milesPerDay IS NULL
    """
    querySQL(stmt=queryForNull)
    # now milesPerDay is never null.
    queryForNotNull = f"""
        UPDATE vehicles
        SET estMiles = (vehicles.miles + 
            vehicles.milesPerDay * DATEDIFF('{getDateTodayStr()}', vehicles.dateLastODO))
        WHERE miles IS NOT NULL
    """
    querySQL(stmt=queryForNotNull)

    # for each service item, if deadline-odoEst < some constant, set the flag.
    servDueThresh = 500
    querySQL(stmt=f"""
        UPDATE serviceSchedule
        SET servDueFlag = TRUE
        WHERE (serviceSchedule.dueAtMiles - (SELECT estMiles FROM vehicles WHERE vehicles.vehicleID = serviceSchedule.vehicleID))
             < {servDueThresh}
    """)


@app.route("/", methods=['GET'])
def serveHome():
    return "Service Reminders Homepage"


# takes the phone number and the content and then passes the appropriate vehicleID and the content (which shoudl be odo) to the updateODO function.
@app.route("/receive_sms", methods=['POST'])
def receiveOdoMsg():
    # don't worry about any input handling except avoiding
    # SQL injection using %s and checking if the user is
    # not in the DB.
    # raises NotInDatabaseError
    def parseRequest():
        # we only care about POSTs from TWILIO so anything else can go ahead and throw some sort of exception
        # just no SQL injection, so use %s
        phone = request.form['From']
        res = querySQL(stmt="""
            SELECT userID FROM users
            WHERE phone = %s
        """, val=(phone,))
        if res == []:
            raise NotInDatabaseError(f"{phone} not in DB")

        userID = res[0][0]

        vehID = getUserUpdateVehicle(userID)
        odo = request.form['Body']

        return vehID, odo

    resp = MessagingResponse()
    maxODO = getMaxTheoValueDecimal(tableName="vehicles", columnName="miles")

    try:
        vehID, odo = parseRequest()
    except NotInDatabaseError:
        errStr = "your phone number is not associated with Service Reminders."
    else:
        if not vehID:
            errStr = "none of your vehicles need an odometer update."
        elif not strIsFloat(odo):
            errStr = "message is not a number"
        elif float(odo) < 0:
            errStr = "can't be negative."
        elif float(odo) > maxODO:
            errStr = f"number can't be more than {maxODO}"
        else:
            # lastly, try to update vehicle's ODO and check for a valueerror
            try:
                updateODO(vehID=vehID, newODO=odo)
            except ValueError:
                errStr = "must be more than your vehicle's last recorded miles."
            else:
                errStr = None

    if errStr:
        resp.message(f"Error updating Odometer: {errStr}")
    else:
        res = querySQL(
            stmt="""
                SELECT vehNickname, year, make, model FROM vehicles
                WHERE vehicleID = %s
            """,
            val=(vehID,)
        )
        nick, year, make, model = res[0]
        resp.message(
            f"Successfully updated the odometer for {nick if nick else "your " + str(year) + " " + make + " " + model}.")

    return Response(str(resp), mimetype='text/xml')


if __name__ == '__main__':
    from sys import argv
    app.run(port=3000)
    # if len(argv) == 2:
    #     run(port=int(argv[1]))
    # else:
    #     run()
