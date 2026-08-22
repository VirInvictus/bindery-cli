import java.io.File;
import java.io.PrintWriter;
import java.util.Scanner;
import java.util.ArrayList;
import java.util.List;
import com.adobe.epubcheck.api.EpubCheck;
import com.adobe.epubcheck.reporting.CheckingReport;

public class FastSweep {
    public static void main(String[] args) throws Exception {
        Scanner scanner = new Scanner(System.in);
        List<String> paths = new ArrayList<>();
        while (scanner.hasNextLine()) {
            String path = scanner.nextLine();
            if (!path.trim().isEmpty()) paths.add(path);
        }
        
        System.out.println("fatals,errors,warnings,path");
        paths.parallelStream().forEach(path -> {
            File epub = new File(path);
            if (!epub.exists()) {
                return;
            }
            try {
                PrintWriter out = new PrintWriter(new java.io.OutputStream() {
                    public void write(int b) {}
                });
                CheckingReport report = new CheckingReport(out, epub.getName());
                EpubCheck check = new EpubCheck(epub, report);
                check.doValidate();
                String res = report.getFatalErrorCount() + "," + report.getErrorCount() + "," + report.getWarningCount() + "," + epub.getCanonicalPath();
                synchronized(System.out) {
                    System.out.println(res);
                }
            } catch (Exception e) {
                // Ignore
            }
        });
    }
}
