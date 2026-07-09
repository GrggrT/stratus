using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace StratusConsole.Converters;

/// <summary>
/// Maps a MonitoringListItem accent ("positive"/"negative"/null) to the badge
/// background brush. Null keeps the neutral theme fill.
/// </summary>
public sealed partial class AccentToBrushConverter : IValueConverter
{
    private static readonly SolidColorBrush Positive =
        new(Color.FromArgb(0xFF, 0x1F, 0x6E, 0x33));
    private static readonly SolidColorBrush Negative =
        new(Color.FromArgb(0xFF, 0xA8, 0x3A, 0x48));
    private static readonly SolidColorBrush Neutral =
        new(Color.FromArgb(0x33, 0x80, 0x80, 0x80));

    public object Convert(object value, Type targetType, object parameter, string language) =>
        value as string switch
        {
            "positive" => Positive,
            "negative" => Negative,
            _ => Neutral,
        };

    public object ConvertBack(object value, Type targetType, object parameter, string language) =>
        throw new NotSupportedException();
}
