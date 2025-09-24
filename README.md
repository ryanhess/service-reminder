mvp
    user goes to webpage inputs phone number
    gets a text message immediately asking for odometer
    gets a message every x amount of days asking for new odo readng.
    have rate
    Notify x days ahead of x service item

Engineering req
    webserver
        -Serve the form to enter user data.
    database
        -When post with phone number comes, update the database with the phone number and the user name.
    twilio account for sending texts
    api routes
    chron job to wait and take action

sep 21, 2025:
desired behavior for handling queries to the user about ODO readings:
query the most out of date vehicle first, and only handling this vehicle.
