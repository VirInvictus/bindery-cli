import java.io.File;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Scanner;
import java.util.ArrayList;
import java.util.List;
import com.adobe.epubcheck.api.EpubCheck;
import com.adobe.epubcheck.reporting.CheckingReport;

public class FastSweepExtract {
    public static void main(String[] args) throws Exception {
        Scanner scanner = new Scanner(System.in);
        List<String> paths = new ArrayList<>();
        while (scanner.hasNextLine()) {
            String path = scanner.nextLine();
            if (!path.trim().isEmpty()) paths.add(path);
        }
        
        paths.parallelStream().forEach(path -> {
            File epub = new File(path);
            if (!epub.exists()) return;
            try {
                StringWriter sw = new StringWriter();
                PrintWriter out = new PrintWriter(sw);
                CheckingReport report = new CheckingReport(out, epub.getName());
                EpubCheck check = new EpubCheck(epub, report);
                check.doValidate();
                out.flush();
                String text = sw.toString();
                if (report.getErrorCount() > 0 || report.getFatalErrorCount() > 0 || report.getWarningCount() > 0) {
                    // Extract error codes (e.g. RSC-005, CSS-008)
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("([A-Z]{3}-\\d{3})").matcher(text);
                    List<String> codes = new ArrayList<>();
                    while (m.find()) {
                        codes.add(m.group(1));
                    }
                    synchronized(System.out) {
                        System.out.println(epub.getCanonicalPath() + " ||| " + String.join(",", codes));
                    }
                }
            } catch (Exception e) {}
        });
    }
}
