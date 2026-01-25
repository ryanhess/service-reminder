import os
from decimal import *
from datetime import date
from typing import Any
from mysql.connector import connect, Error
from mysql.connector.connection import MySQLConnection
from fastapi import FastAPI, Request, Form, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
# import DB_Builder
import traceback

ODOPROMPTINTERVAL = 7  # the number of days to wait before prompting a regular ODO reading

#error messages
BELOWZERO = '{what} cannot be negative.'
ODOBELOWZERO = 'Odometer cannot be negative'
ABOVEMAX = 'cannot be more than {max}'
BELOWMIN = 'cannot be less than {min}'
NOTANUMBER = "{what} is not a number"
ODONOTANUMBER = 'Odometer reading not a number.'
ILLEGALDUPLICATE = '{param} is already in the database and cannot be duplicated.'
ILLEGALDUPLICATESERVICE = 'A service item called "{desc}" already exists for this vehicle.'
ODODECREASING = "New odometer can't be less than current odometer"
PARAMNOTFOUNDINREQUEST = 'requred parameter "{param}" missing from request'
FORMFIELDBLANK = "'{field}' can't be blank"
NOELIGIBLEVEHICLE = "no eligible vehicle for user {userID}"
NOTINDB = '{type} {id} not found in DB'
INVALIDPARAM = 'parameter is not a valid {param}'
UNCAUGHTEXCEPTION = 'uncaught exception raised'
#text messages
PHONENOTINDBSMS = "your phone number is not associated with Service Reminders."
SERVICENOTIFICATION = '{username}, {displayName} is due for item: "{desc}" at {dueAt} miles.'
NOELIGIBLEVEHICLESMS = "none of your vehicles need an odometer update."
SUCCESSFULODOUPDATESMS = "Successfully updated the odometer"

app = FastAPI()
templates = Jinja2Templates(directory="templates")


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
    result = querySQL("""
        SELECT numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = "service_reminders_app"
        AND table_name = %s
        AND column_name = %s
        AND data_type = "decimal"
    """, val=(tableName, columnName))
    if not result.queryResultValues:
        return "Column is not Decimal type"
    else:
        digitsLeftDecimal = result.queryResultValues[0][0] - 1
        digitsRightDecimal = result.queryResultValues[0][1]
        return 10 ** digitsLeftDecimal - 10 ** (-1 * digitsRightDecimal)

### custom exceptions ###

# exception thrown when a given row is not found in DB


class NotInDatabaseError(Exception):
    pass


class FormInputError(Exception):
    pass


class DuplicateItemError(Exception):
    pass


class SqlQueryResult():
    def __init__(self):
        self.queryResultValues: list | None = None
        self.insertedRowID: int = 0


# function to execute SQL query in a safe container, opening and closing the connection and checking for errors along the way.
# returns the result of a query if there is one.
def querySQL(
        stmt: str = "",
        val: Any = None,
        many: bool = False,
        connection: MySQLConnection | None = None
) -> SqlQueryResult:
    """
    Executes a MYSQL query given by the string.
    
    :param stmt: SQL query string
    :param val: values to insert using placeholders
    :param many: (False by default) the query many times for the array of values array of values
    :param connection: (None by default) a MySQLConnection object to use for the query. Created if not specified.
    
    :returns:
        a dictionary with the returned data as a list and the inserted row id, or zero if no row was inserted.
        If no results are returned by the query, the returned data is None.
    
    :raises: Exception if the query throws an error in MySQL.
    """
    
    try:
        queryResult = SqlQueryResult()

        if connection is None:
            connection = connect(
                host="localhost",
                user="serv-rem-dev",
                password="password",
                database="service_reminders_app"
            )

        with connection:
            c1 = connection.cursor()

            if many:
                c1.executemany(stmt, val)
            else:
                c1.execute(stmt, val)

            queryResult.queryResultValues = c1.fetchall()

            # check if the query inserted a row and get its id
            # (this check only works with current, auto-increment PKs)
            if c1.lastrowid:
                queryResult.insertedRowID = c1.lastrowid

            connection.commit()

            return queryResult
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

    if result.queryResultValues:
        return result.queryResultValues[0][0]
    else:
        result = querySQL(f'''
            SELECT vehicleID FROM vehicles
            WHERE userID = {userID}
            AND DATEDIFF('{getDateToday()}', dateLastODO) > {ODOPROMPTINTERVAL}
            ORDER BY dateLastODO ASC
            LIMIT 1
        ''')
        if not result.queryResultValues:
            return None
        else:
            return result.queryResultValues[0][0]


def promptUserForOneVeh(usrID=0):
    vehID = getUserUpdateVehicle(usrID)
    if vehID is None:
        raise NotInDatabaseError(NOELIGIBLEVEHICLE)

    queryResult = querySQL(stmt=f'''
            SELECT displayName
            FROM vehicles
            WHERE vehicleID = {vehID}
    ''')

    displayName = queryResult.queryResultValues[0]

    # we need the user name and the phone number from the user.
    queryResult = querySQL(stmt=f'''
        SELECT username, phone FROM users
        WHERE userID = {usrID}
    ''')
    (username, phone) = queryResult.queryResultValues[0]

    msg = f"""Hey {username}, Service Reminders here. Please reply with an odometer reading for {displayName}."""

    return phone, msg


# def:
# update a vehicle odometer in database with the given odo
# this should check that the new ODO reading is greater than the previous ODO reading. Should reply to the user confirming the reading or prompting again if the reading contains an error.
# Calculate and store a new average miles per day given the prev ODO reading and the days since the last ODO reading.
def updateODO(vehID=0, newODO=0):
    '''
    :raises TypeError if called with not a number.
    :raises NotInDatabaseError if the veh doesnt exist
    :raises ValueError if the param is less than the current odo.
    :raises ValueError also if the param is less than zero.
    '''
    today = getDateToday()

    res = querySQL(stmt='''
        SELECT miles, dateLastODO, milesPerDay 
        FROM vehicles
        WHERE vehicleID = %s
    ''', val=(vehID, ))

    if not res.queryResultValues:
        raise NotInDatabaseError(NOTINDB.format(type='vehicle', id=vehID))

    curMiles, curOdoDate, curMilesPerDay = res.queryResultValues[0]

    # In updateODO we want to detect if current odo is None. We need to make a sepcial case.
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

    try:
        newODO = float(newODO)
    except ValueError as e:
        raise TypeError(NOTANUMBER)
    
    if newODO < 0:
        raise ValueError(ODOBELOWZERO)
        
    if newODO < curMiles:
        raise ValueError(ODODECREASING)

    if newODO is None:
        newODO = 0

    # we have to account for if the odo is updated again on the same day.
    try:
        newMilesPerDay = (newODO - float(curMiles)) / (today - curOdoDate).days
    except ZeroDivisionError:
        newMilesPerDay = curMilesPerDay

    querySQL(stmt='''
        UPDATE vehicles
        SET miles = %s, dateLastODO = %s, milesPerDay = %s
        WHERE vehicleID = %s
    ''', val=( round(newODO,1), today, newMilesPerDay, vehID ))


# def:
# update the records indicating a service was done at a given miles
# should remove the service due flag, update the mileage deadline, and update the ODO for the vehicle only if this ODO is greater than the ODO stored for the vehicle.
def updateServiceDone(itemID: int, itemODO: float):
    '''
    :raises NotInDatabaseError if item doesnt exist.
    Does not raise any exception if the itemODO is less than the veh parent miles
    it will just not update the parent veh in that case.
    :raises TypeError if the number cant be cast to a float
    :raises ValueError if the number is less than 0
    or greater than the max value allowable in the DB - service interval.
    '''

    res = querySQL(stmt='''
        SELECT serviceInterval, milesLastDone FROM serviceSchedule
        WHERE itemID = %s
    ''', val=(itemID, ))
    if not res.queryResultValues:
        raise NotInDatabaseError(NOTINDB)
    interval, lastMiles = res.queryResultValues[0]

    res = querySQL(f"""
        SELECT vehicleID, miles FROM vehicles
        WHERE vehicles.vehicleID = (SELECT vehicleID FROM serviceSchedule WHERE serviceSchedule.itemID = {itemID})
    """)
    vehID, parentMiles = res.queryResultValues[0]

    # check for not the right type
    try:
        itemODO = float(itemODO)
    except ValueError:
        raise TypeError(NOTANUMBER)
    
    if itemODO < 0:
        raise ValueError(BELOWZERO.format(what='itemODO'))
    elif itemODO > (getMaxTheoValueDecimal(tableName='serviceSchedule', columnName='dueAtMiles') - float(interval)):
        raise ValueError(ABOVEMAX + str(getMaxTheoValueDecimal(tableName='serviceSchedule', columnName='dueAtMiles') - interval) + ' miles')
    elif itemODO < lastMiles:
        raise ValueError(ODODECREASING + lastMiles + 'miles, when this service was last done.')

    # update the miles of the parent vehicle, only if the new ODO is greater than the previous ODO.
    # dont need exceptions to percolate up from here for miles being below parent miles.
    # so check that here.
    if not parentMiles or itemODO > parentMiles:
        # update the miles of the parent vehicle.
        updateODO(vehID, itemODO)

    # remove the service flag.
    querySQL('''
        UPDATE serviceSchedule
        SET milesLastDone = %s, servDueFlag = FALSE
        WHERE itemID = %s
    ''', val=(itemODO, itemID))


# def:
# check the database for service that is due and notify the relevant user. The caller of this function sets the frequency of the reminders.
def notifyOneService(serviceItemID):
    res = querySQL(stmt='''
        SELECT userID, vehicleID, description, dueAtMiles FROM serviceSchedule
        WHERE itemID = %s
    ''', val=(serviceItemID, ))

    if not res.queryResultValues:
        raise NotInDatabaseError(NOTINDB.format(type='service', id=serviceItemID))
    usrID, vehID, desc, dueAt = res.queryResultValues[0]

    res = querySQL(stmt=f"""
        SELECT username, phone FROM users
        WHERE userID = {usrID}
    """)
    username, phone = res.queryResultValues[0]

    res = querySQL(stmt=f"""
        SELECT displayName FROM vehicles
        WHERE vehicleID = {vehID}
    """)

    displayName = res.queryResultValues[0][0]

    msg = SERVICENOTIFICATION.format(username=username, 
        displayName=displayName, desc=desc, dueAt=dueAt)

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
    for item in flaggedItems.queryResultValues:
        phone, msg = notifyOneService(item[0])

        # send the message.
        sendSMS(recip=phone, msg=msg)

    return flaggedItems.queryResultValues


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
    for usr in queryResult.queryResultValues:
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


### API Routes ###

# takes the phone number and the content and then passes the appropriate vehicleID and the content (which shoudl be odo) to the updateODO function.
@app.post("/receive_sms")
def receiveOdoMsg(From: str = Form(...), Body: str = Form(...)):
    # don't worry about any input handling except avoiding
    # SQL injection using %s and checking if the user is
    # not in the DB.
    # raises NotInDatabaseError
    def parseRequest(phone, odo_body):
        # we only care about POSTs from TWILIO so anything else can go ahead and throw some sort of exception
        # just no SQL injection, so use %s
        res = querySQL(stmt="""
            SELECT userID FROM users
            WHERE phone = %s
        """, val=(phone,))
        if not res.queryResultValues:
            raise NotInDatabaseError(NOTINDB.format(type='user', id=phone))

        userID = res.queryResultValues[0][0]

        vehID = getUserUpdateVehicle(userID)

        return vehID, odo_body

    resp = MessagingResponse()
    maxODO = getMaxTheoValueDecimal(tableName="vehicles", columnName="miles")

    try:
        vehID, odo = parseRequest(From, Body)
    except NotInDatabaseError:
        errStr = PHONENOTINDBSMS
    else:
        if not vehID:
            errStr = NOELIGIBLEVEHICLESMS
        elif not strIsFloat(odo):
            errStr = ODONOTANUMBER
        elif float(odo) < 0:
            errStr = ODOBELOWZERO
        elif float(odo) > maxODO:
            errStr = ABOVEMAX.format(max=maxODO)
        else:
            # lastly, try to update vehicle's ODO and check for a valueerror
            try:
                updateODO(vehID=vehID, newODO=odo)
            except ValueError:
                errStr = ODODECREASING
            else:
                errStr = None

    if errStr:
        resp.message(f"Error updating Odometer: {errStr}")
    else:
        res = querySQL(
            stmt="""
                SELECT displayName FROM vehicles
                WHERE vehicleID = %s
            """,
            val=(vehID,)
        )
        displayName = res.queryResultValues[0][0]
        resp.message(SUCCESSFULODOUPDATESMS + f' for {displayName}')

    return Response(content=str(resp), media_type='text/xml')


### WEB UI handler functions ###
# do all the input handling here. if bad input, raise an exception
def handleNewUserPOST(username: str, phone: str):
    # input handling and cleaning up here.
    if 'f-you' in phone or 'whatever' in username:
        raise FormInputError('you messed up, ya doof!')

    # now check if the username or phone number already exists and raise an error for each. Can't have any duplicate phone numbers.
    res = querySQL('''
        SELECT userID FROM users
        WHERE username = %s
    ''', val=(username,))
    if res.queryResultValues:
        raise DuplicateItemError(ILLEGALDUPLICATE.format(param='username'))

    res = querySQL('''
        SELECT userID FROM users
        WHERE phone = %s
    ''', val=(phone,))
    if res.queryResultValues:
        raise DuplicateItemError(ILLEGALDUPLICATE.format(param='phone'))

    # finally, with the cleaned and validated data, add it to the database and return the cleaned data.
    try:
        newUserID = querySQL(stmt='''
            INSERT INTO users (username, phone)
            VALUES (%s, %s)
        ''', val=(username, phone)).insertedRowID
    except Exception as e:
        # DEBUG
        raise e

    return {'userID': newUserID, 'username': username, 'phone': phone}


# in these cases, we want to check that it is a valid ID and that
# it exists in the DB.
def validateUserIdInURL(userID):
    try:
        userID = int(userID)
    except ValueError:
        raise ValueError(INVALIDPARAM.format(param='userID'))

    res = querySQL(stmt='''
        SELECT userID FROM users
        WHERE userID = %s
    ''', val=(userID,))
    if not res.queryResultValues:
        raise NotInDatabaseError(NOTINDB.format(user=userID))

    return userID


# in these cases, we want to check that it is a valid ID and that
# it exists in the DB.
def validateVehIdInURL(vehID):
    try:
        vehID = int(vehID)
    except ValueError:
        raise ValueError(INVALIDPARAM.format(param='vehID'))

    res = querySQL(stmt='''
        SELECT vehicleID FROM vehicles
        WHERE vehicleID = %s
    ''', val=(vehID,))
    if not res.queryResultValues:
        raise NotInDatabaseError(NOTINDB.format(type='vehicle', id=vehID))

    return vehID

def validateServiceItemIdInUrl(itemID: int):
    try:
        itemID = int(itemID)
    except ValueError:
        raise ValueError(INVALIDPARAM.format(param='itemID'))

    res = querySQL(stmt='''
        SELECT itemID FROM serviceSchedule
        WHERE itemID = %s
    ''', val=(itemID, ))
    if not res.queryResultValues:
        raise NotInDatabaseError(NOTINDB.format(type='service item', id=itemID))
    
    return itemID


# validates the post request, adds data to DB,
# returns the nickname, year make model for the car
# raises exceptions if bad input
def handleNewVehiclePOST(userID, nick: str, year: str, make: str, model: str, miles: str):
    print(f"handleNewVehiclePOST called with userID={userID}, nick={nick}, year={year}, make={make}, model={model}, miles={miles}")
    if userID == 6:
        breakpoint()
    try:
        userID = validateUserIdInURL(userID)
    except Exception as e:
        raise e

    # nickname - empty is OK (will use year make model as display name)

    # year
    # check that it is present and will convert to a YEAR type in SQL
    if year == '' or not year:
        raise FormInputError(FORMFIELDBLANK.format(field='year'))

    # try casting the input into a SQL year datatype
    if year == '1':
        breakpoint()
    res = querySQL('''
        SELECT CAST(%s AS YEAR)
    ''', val=(year, ))
    if not res.queryResultValues[0][0]:
        raise FormInputError(INVALIDPARAM.format(param='year'))

    # make
    if make == '':
        raise FormInputError(FORMFIELDBLANK.format(field='make'))

    # model
    if model == '':
        raise FormInputError(FORMFIELDBLANK.format(field='model'))

    result = querySQL(stmt='''
        INSERT INTO vehicles
        (userID, vehNickname, make, model, year)
        VALUES (%s, %s, %s, %s, %s)
    ''', val=(userID, nick, make, model, year))
    newVehID = result.insertedRowID

    result = querySQL(stmt=f'''
        SELECT displayName FROM vehicles
        WHERE vehicleID = {newVehID}
    ''')

    dispName = result.queryResultValues[0][0]

    # now try to add the odometer reading if provided
    if len(miles) > 0:
        try:
            updateODO(vehID=newVehID, newODO=miles)
        except TypeError:
            raise FormInputError(NOTANUMBER.format(what='miles'))
        except ValueError as e:
            if ODOBELOWZERO in str(e):
                raise FormInputError(ODOBELOWZERO)
            else:
                # if value error is being raised for any other reason,
                # that's an uncaught exception and 400.
                raise Exception(UNCAUGHTEXCEPTION)
        except Exception as e:
            raise e

    # for now dont check for duplicate vehicles.

    return {'id': newVehID, 'displayName': dispName, 'miles': miles}

    # miles, if it is empty string, then leave miles NULL


# Handle the new service form, validate inputs, and add as a new service.
def handleNewServicePOST(vehicleID: int, description: str, interval: str, milesLastDone: str):
    print(f"handleNewServicePOST called with vehicleID={vehicleID}, description={description}, interval={interval}, milesLastDone={milesLastDone}")

    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except Exception as e:
        raise e

    # description - check that it is present
    if description == '':
        raise FormInputError(FORMFIELDBLANK.format(field='description'))

    # interval - check that it is present and valid
    if interval == '':
        raise FormInputError(FORMFIELDBLANK.format(field='interval'))

    # try casting the input into a float
    try:
        interval_float = float(interval)
        if interval_float <= 0:
            raise ValueError()
    except ValueError:
        raise FormInputError(NOTANUMBER.format(what='interval'))

    # check that miles last done is a valid (positive) number
    milesLastDone_val = milesLastDone

    if milesLastDone_val and milesLastDone_val != '':
        try:
            milesLastDone_val = float(milesLastDone_val)
            if milesLastDone_val < 0:
                raise FormInputError(BELOWZERO.format(what='milesLastDone'))
        except ValueError:
            raise FormInputError(NOTANUMBER.format(what='Miles Last Done'))
    else:
        milesLastDone_val = 0

    # check if an item whose description matches, is already in the DB.
    # if so, raise the duplicate item error.
    result = querySQL(stmt='''
        SELECT description FROM serviceSchedule
        WHERE description = %s
        AND vehicleID = %s
    ''', val=(description, vehicleID))

    # if there is more than an empty array in the result,
    if result.queryResultValues:
        raise DuplicateItemError(ILLEGALDUPLICATESERVICE.format(desc=description))

    result = querySQL(stmt='''
        SELECT userID FROM vehicles
        WHERE vehicleID = %s
    ''', val=(vehicleID, ))
    userID = result.queryResultValues[0][0]

    result = querySQL(stmt='''
        INSERT INTO serviceSchedule
        (vehicleID, userID, description, serviceInterval, milesLastDone)
        VALUES (%s, %s, %s, %s, %s)
    ''', val=(vehicleID, userID, description, interval_float, milesLastDone_val))

    return {'description': description, 'interval': interval_float}


def handleUpdateOdoPOST(vehicleID: int, miles: str):
    print(f"handleUpdateOdoPOST called with vehicleID={vehicleID}, miles={miles}")

    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except Exception as e:
        raise e

    if miles == '':
        raise FormInputError(FORMFIELDBLANK.format(field='miles'))

    # updateODO does the rest of the input checking.
    try:
        updateODO(vehID=vehicleID, newODO=miles)
    except ValueError as v:
        if ODODECREASING in str(v):
            raise FormInputError(ODODECREASING)

    except TypeError:
        raise FormInputError(ODONOTANUMBER)
    except Exception as e:
        raise e

    return miles


def handleUpdateServDonePOST(itemID: int, miles: str):
    print(f"handleUpdateServDonePOST called with itemID={itemID}, miles={miles}")

    try:
        itemID = validateServiceItemIdInUrl(itemID)
    except Exception as e:
        raise e

    # check that miles is present
    if miles == '':
        raise FormInputError(FORMFIELDBLANK.format(field='miles'))

    # updateServiceDone with error checking
    try:
        updateServiceDone(itemID=itemID, itemODO=miles)
    except ValueError as e:
        raise FormInputError(f'{e}')
    except TypeError:
        raise FormInputError(ODONOTANUMBER)
    except Exception as e:
        raise e

    return miles


### WEB UI ROUTES ###

# Serves the homepage, which consists of a welcome message
# and nav links to Home and Users
@app.get("/", response_class=HTMLResponse)
def serveHome(request: Request):
    return templates.TemplateResponse(request, "index.html")


# USERS #

# serves the Users main page
# Which consists of a title,
# a list of users which are links to /Users/[username]
# and a link called "New User" which links to /Users/New
@app.get("/Users", response_class=HTMLResponse)
def serveUsersList(request: Request):
    # retrieve a list of usernames
    res = querySQL('SELECT userID, username FROM users')
    users = []

    for item in res.queryResultValues:
        user = {'userID': item[0], 'username': item[1]}
        users.append(user)

    return templates.TemplateResponse(request, "users.html", {"users": users})


@app.get("/Users/New", response_class=HTMLResponse)
def newUserUIGet(request: Request):
    newUserForm = 'new_user_form.html'
    return templates.TemplateResponse(request, newUserForm, {"error": False})


@app.post("/Users/New", response_class=HTMLResponse)
def newUserUIPost(request: Request, username: str = Form(...), phone: str = Form(...)):
    newUserForm = 'new_user_form.html'
    newUserConf = 'new_user_submitted.html'
    try:
        userInfo = handleNewUserPOST(username, phone)
    except FormInputError as f:
        return templates.TemplateResponse(request, newUserForm, {"errorMessage": str(f)})
    except DuplicateItemError as d:
        return templates.TemplateResponse(request, newUserForm, {"errorMessage": str(d)})

    print(f"username={username}, phone={phone}")
    return templates.TemplateResponse(request, newUserConf, {"userInfo": userInfo})


# show individual user
# Should show a list of vehicles by nickname,
# year, make model. Clicking on a vehicle takes
# you to the page for that vehicle.
# get the list of vehicles for that user,
# where each veh is a dictionary of id, nickname, make, model, year, miles.
@app.get("/Users/{userID}", response_class=HTMLResponse)
def serveSingleUserPage(request: Request, userID: str):
    # retrieve the user given by userID, meaning a list of veh for that user.

    # get the username of the user to put in the header. Note that the userID param
    # is a user-entered value through the URL.
    try:
        userID = validateUserIdInURL(userID)
    except Exception as e:
        # if there is any issue with the input here, return page not found.
        return Response(status_code=404)

    res = querySQL('''
        SELECT username FROM users
        WHERE userID = %s
    ''', val=(userID, ))
    username = res.queryResultValues[0][0]

    res = querySQL('''
        SELECT vehicleID, vehNickname, make,
            model, year, miles, dateLastOdo
        FROM vehicles
        WHERE userID = %s
    ''', val=(userID, ))
    vehicles = []

    for item in res.queryResultValues:
        veh = {
            'id': item[0],
            'nick': item[1],
            'make': item[2],
            'model': item[3],
            'year': item[4],
            'miles': item[5],
            'dateLastOdo': item[6]
        }
        vehicles.append(veh)

    return templates.TemplateResponse(request, "single_user.html", {"user": {'id': userID, 'name': username}, "vehicles": vehicles})


# VEHICLES #

# should show the vehicle info in one div
# then a button to add a service item
# then another table with all the service items listed
@app.get('/Vehicles/{vehicleID}', response_class=HTMLResponse)
def serveSingleVehiclePage(request: Request, vehicleID: str):
    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except:
        return Response(status_code=404)

    res = querySQL(f'''
        SELECT vehicleID, displayName, miles, dateLastODO, estMiles
        FROM vehicles
        WHERE vehicleID = {vehicleID}
    ''')
    res = res.queryResultValues[0]
    vehicle = {
        'id': res[0],
        'displayName': res[1],
        'miles': res[2],
        'dateLastODO': res[3],
        'estMiles': res[4]
    }

    res = querySQL(f'''
        SELECT itemID, description, serviceInterval, dueAtMiles
        FROM serviceSchedule
        WHERE vehicleID = {vehicleID}
    ''')

    serviceSched = []
    for result in res.queryResultValues:
        serviceSched.append({
            'id': result[0],
            'description': result[1],
            'serviceInterval': result[2],
            'dueAtMiles': result[3]
        })

    return templates.TemplateResponse(request, "single_vehicle.html", {"vehicle": vehicle, "serviceSched": serviceSched})


@app.get('/Users/{userID}/New-Vehicle', response_class=HTMLResponse)
def newVehicleUIGet(request: Request, userID: str):
    newVehForm = 'new_vehicle_form.html'
    try:
        userID = validateUserIdInURL(userID)
    except:
        return Response(status_code=404)

    res = querySQL('''
        SELECT userID, username FROM users
        WHERE userID = %s
    ''', val=(userID,))
    user = {'id': res.queryResultValues[0][0], 'username': res.queryResultValues[0][1]}

    return templates.TemplateResponse(request, newVehForm, {"user": user})


@app.post('/Users/{userID}/New-Vehicle', response_class=HTMLResponse)
def newVehicleUIPost(
    request: Request,
    userID: str,
    nickname: str = Form(""),
    year: str = Form(""),
    make: str = Form(""),
    model: str = Form(""),
    miles: str = Form("")
):
    newVehForm = 'new_vehicle_form.html'
    newVehConf = 'new_vehicle_conf.html'
    try:
        userID = validateUserIdInURL(userID)
    except:
        return Response(status_code=404)

    res = querySQL('''
        SELECT userID, username FROM users
        WHERE userID = %s
    ''', val=(userID,))
    user = {'id': res.queryResultValues[0][0], 'username': res.queryResultValues[0][1]}

    try:
        vehicle = handleNewVehiclePOST(userID, nickname, year, make, model, miles)
    except FormInputError as f:
        return templates.TemplateResponse(request, newVehForm, {"user": user, "errorMessage": str(f)})
    except DuplicateItemError as d:
        return templates.TemplateResponse(request, newVehForm, {"user": user, "errorMessage": str(d)})
    except Exception as e:
        print(e)
        return Response(status_code=400)

    return templates.TemplateResponse(request, newVehConf, {"user": user, "vehicle": vehicle})


@app.get('/Vehicles/{vehicleID}/New-Service', response_class=HTMLResponse)
def newServiceUIGet(request: Request, vehicleID: str):
    newServForm = 'new_service_form.html'
    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except:
        return Response(status_code=404)

    return templates.TemplateResponse(request, newServForm, {"vehicleID": vehicleID, "error": False})


@app.post('/Vehicles/{vehicleID}/New-Service', response_class=HTMLResponse)
def newServiceUIPost(
    request: Request,
    vehicleID: str,
    description: str = Form(""),
    interval: str = Form(""),
    milesLastDone: str = Form("")
):
    newServForm = 'new_service_form.html'
    newServConf = 'new_service_submitted.html'
    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except:
        return Response(status_code=404)

    try:
        newService = handleNewServicePOST(vehicleID, description, interval, milesLastDone)
    except FormInputError as f:
        return templates.TemplateResponse(request, newServForm, {"vehicleID": vehicleID, "error": True, "errorMessage": str(f)})
    except DuplicateItemError as d:
        return templates.TemplateResponse(request, newServForm, {"vehicleID": vehicleID, "error": True, "errorMessage": str(d)})
    except Exception as e:
        print(e)
        return Response(status_code=400)

    return templates.TemplateResponse(request, newServConf, {"vehicleID": vehicleID, "newService": newService})


@app.get('/Vehicles/{vehicleID}/Update-Odometer', response_class=HTMLResponse)
def updateOdoUIGet(request: Request, vehicleID: str):
    updateODOForm = 'update_odo_form.html'
    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except:
        return Response(status_code=404)

    res = querySQL(stmt='''
        SELECT vehicleID, displayName, miles FROM vehicles
        WHERE vehicleID = %s
    ''', val=(vehicleID, ))
    vehicle = {'id': res.queryResultValues[0][0], 'displayName': res.queryResultValues[0][1], 'miles': res.queryResultValues[0][2]}

    return templates.TemplateResponse(request, updateODOForm, {"vehicle": vehicle})


@app.post('/Vehicles/{vehicleID}/Update-Odometer', response_class=HTMLResponse)
def updateOdoUIPost(request: Request, vehicleID: str, miles: str = Form("")):
    updateODOForm = 'update_odo_form.html'
    updateODOConf = 'update_odo_confirmation.html'
    try:
        vehicleID = validateVehIdInURL(vehicleID)
    except:
        return Response(status_code=404)

    res = querySQL(stmt='''
        SELECT vehicleID, displayName, miles FROM vehicles
        WHERE vehicleID = %s
    ''', val=(vehicleID, ))
    vehicle = {'id': res.queryResultValues[0][0], 'displayName': res.queryResultValues[0][1], 'miles': res.queryResultValues[0][2]}

    try:
        vehicle['miles'] = handleUpdateOdoPOST(vehicleID, miles)
    except FormInputError as f:
        return templates.TemplateResponse(request, updateODOForm, {"vehicle": vehicle, "errorMessage": str(f)})
    except DuplicateItemError as d:
        return templates.TemplateResponse(request, updateODOForm, {"vehicle": vehicle, "errorMessage": str(d)})
    except Exception as e:
        print(e)
        return Response(status_code=400)

    return templates.TemplateResponse(request, updateODOConf, {"vehicle": vehicle})


@app.get('/Service/{itemID}/Update-Service-Done', response_class=HTMLResponse)
def updateServiceDoneUIGet(request: Request, itemID: str):
    servDoneForm = 'service_done_form.html'
    try:
        itemID = validateServiceItemIdInUrl(itemID)
    except:
        return Response(status_code=404)

    res = querySQL(stmt='''
        SELECT itemID, vehicleID, description FROM serviceSchedule
        WHERE itemID = %s
    ''', val=(itemID, ))
    serviceItem = {'id': res.queryResultValues[0][0],
                   'vehicleID': res.queryResultValues[0][1],
                   'description': res.queryResultValues[0][2],
                   'milesDoneAt': 0}

    return templates.TemplateResponse(request, servDoneForm, {"serviceItem": serviceItem})


@app.post('/Service/{itemID}/Update-Service-Done', response_class=HTMLResponse)
def updateServiceDoneUIPost(request: Request, itemID: str, miles: str = Form("")):
    servDoneForm = 'service_done_form.html'
    servDoneConf = 'service_done_confirmation.html'
    try:
        itemID = validateServiceItemIdInUrl(itemID)
    except:
        return Response(status_code=404)

    res = querySQL(stmt='''
        SELECT itemID, vehicleID, description FROM serviceSchedule
        WHERE itemID = %s
    ''', val=(itemID, ))
    serviceItem = {'id': res.queryResultValues[0][0],
                   'vehicleID': res.queryResultValues[0][1],
                   'description': res.queryResultValues[0][2],
                   'milesDoneAt': 0}

    try:
        serviceItem['milesDoneAt'] = handleUpdateServDonePOST(itemID, miles)
    except FormInputError as f:
        traceback.print_exc()
        return templates.TemplateResponse(request, servDoneForm, {"serviceItem": serviceItem, "errorMessage": str(f)})
    except DuplicateItemError as d:
        traceback.print_exc()
        return templates.TemplateResponse(request, servDoneForm, {"serviceItem": serviceItem, "errorMessage": str(d)})
    except Exception as e:
        traceback.print_exc()
        print(e)
        return Response(status_code=400)

    return templates.TemplateResponse(request, servDoneConf, {"serviceItem": serviceItem})


### Running the server ###


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=3000, reload=True)
