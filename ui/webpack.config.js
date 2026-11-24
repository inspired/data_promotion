const path = require('path');
const webpack = require('webpack');

// Builds the Data Promotion page into the promotion_control app. Splunk's built-in
// pages/splunk_ui_app.html template loads appserver/static/pages/<view name>.js.
module.exports = (env, argv) => ({
    entry: { data_promotion: path.join(__dirname, 'src/index.jsx') },
    output: {
        path: path.join(__dirname, '..', 'promotion_control', 'appserver', 'static', 'pages'),
        filename: '[name].js',
        clean: true,
    },
    resolve: { extensions: ['.js', '.jsx'] },
    module: {
        rules: [
            {
                test: /\.jsx?$/,
                exclude: /node_modules/,
                use: 'babel-loader',
            },
        ],
    },
    plugins: [
        // moment ships every locale; Splunk Web provides its own i18n.
        new webpack.IgnorePlugin({ resourceRegExp: /^\.\/locale$/, contextRegExp: /moment$/ }),
    ],
    devtool: argv.mode === 'production' ? false : 'eval-source-map',
    performance: { hints: false },
});
